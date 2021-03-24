"""Management of OPC-UA client part."""

import asyncio
import logging
from typing import Any

import asyncua
import tenacity
from asyncua import ua
from asyncua.common.subscription import SubscriptionItemData
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from asyncua.ua.uaerrors import UaStatusCodeError
from yarl import URL

from .config import OPCSettings
from .frontend_messaging import CentrifugoProxyServer, FrontendMessagingWriter
from .influxdb import InfluxDBWriter
from .library import AsyncTask
from .messages import LinkStatus, OPCDataChangeMessage, OPCStatusMessage

SIMATIC_NAMESPACE_URI = "http://www.siemens.com/simatic-s7-opcua"

_logger = logging.getLogger(__name__)


class OPCUAClient(AsyncTask):
    """OPC-UA client task."""

    logger = _logger
    purpose = "OPC-UA client"

    def __init__(
        self,
        config: OPCSettings,
        centrifugo_proxy_server: CentrifugoProxyServer,
        influx_writer: InfluxDBWriter,
        frontend_messaging_writer: FrontendMessagingWriter,
    ):
        """Initializes OPC-UA client task.

        Args:
            config: OPC-UA related configuration options.
            centrifugo_proxy_server: Centrifugo proxy server task instance.
            influx_writer: InfluxDB writer task instance.
            frontend_messaging_writer: Frontend messaging task instance.
        """
        self._config = config
        self._centrifugo_proxy_server = centrifugo_proxy_server
        self._frontend_messaging_writer = frontend_messaging_writer
        self._influx_writer = influx_writer
        self._status = LinkStatus.Down

    async def _create_opc_client(self) -> asyncua.Client:
        server_url = URL(self._config.server_url)
        sanitized_server_url = server_url.with_user(None)
        client = asyncua.Client(url=str(sanitized_server_url))
        if server_url.user is not None:
            client.set_user(server_url.user)
            client.set_password(server_url.password)
        if self._config.cert_file is not None:
            await client.set_security(
                SecurityPolicyBasic256Sha256,
                str(self._config.cert_file),
                str(self._config.private_key_file),
            )
        return client

    async def _task(self) -> None:
        client = await self._create_opc_client()

        async with client:
            ns = await client.get_namespace_index(SIMATIC_NAMESPACE_URI)

            simatic_types_var = await client.nodes.opc_binary.get_child(
                f"{ns}:SimaticStructures"
            )
            await client.load_type_definitions([simatic_types_var])

            sub_nodes_ids = self._config.monitor_nodes + self._config.record_nodes
            sub_nodes = [
                client.get_node(f"ns={ns};s={node_id}") for node_id in sub_nodes_ids
            ]
            subscription = await client.create_subscription(1000, self)
            sub_results = await subscription.subscribe_data_change(sub_nodes)
            for index, result in enumerate(sub_results):
                try:
                    result.check()
                except AttributeError:  # subscription succeeded (result is an integer)
                    pass
                except UaStatusCodeError:  # subscription failed (result is asynua.ua.StatusCode)
                    _logger.exception("Error subscribing to node %s", sub_nodes[index])
                    raise

            server_state = client.get_node(ua.ObjectIds.Server_ServerStatus_State)

            while True:
                await asyncio.sleep(5)
                await server_state.read_data_value()

    def set_status(self, status: LinkStatus) -> None:
        """Sets the status of OPC-UA server link.

        Args:
            status: The OPC-UA server link status.
        """
        if status != LinkStatus.Up:
            self._centrifugo_proxy_server.clear_last_opc_data()
        message = OPCStatusMessage(payload=status)
        self._centrifugo_proxy_server.last_opc_status = message
        if status != self._status:
            self._status = status
            self._frontend_messaging_writer.put(message)

    def datachange_notification(
        self,
        node: asyncua.Node,
        val: Any,
        data: SubscriptionItemData,  # noqa: U100
    ) -> None:
        """OPC-UA data change handler. Implements subscription handler."""
        node_id = node.nodeid.Identifier
        _logger.debug("datachange_notification for %s %s", node_id, val)
        self.set_status(LinkStatus.Up)
        message = OPCDataChangeMessage(node_id=node_id, ua_object=val)
        self._centrifugo_proxy_server.record_last_opc_data(message)
        self._frontend_messaging_writer.put(message)
        if node_id in self._config.record_nodes:
            self._influx_writer.put(message)

    def before_sleep(self, retry_state: tenacity.RetryCallState) -> None:
        """Callback to be called before sleeping on each task retrying."""
        self.set_status(LinkStatus.Down)
        exc = retry_state.outcome.exception()
        _logger.info(
            "Retrying OPC client task in %s seconds as it raised %s: %s",
            retry_state.next_action.sleep,
            type(exc).__name__,
            exc,
        )

    async def task(self) -> None:
        """Implements OPC-UA client asynchronous task."""
        retryer = tenacity.AsyncRetrying(
            wait=tenacity.wait_fixed(self._config.retry_delay),
            retry=(
                tenacity.retry_if_exception_type(OSError)
                | tenacity.retry_if_exception_type(asyncio.TimeoutError)
            ),
            before_sleep=self.before_sleep,
        )
        await retryer.call(self._task)
