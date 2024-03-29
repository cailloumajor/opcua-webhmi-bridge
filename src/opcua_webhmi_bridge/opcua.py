"""Management of OPC-UA client part."""

import asyncio
import logging
import time
from typing import Any, NoReturn

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
from .messages import LinkStatus, OPCDataMessage, OPCStatusMessage

SIMATIC_NAMESPACE_URI = "http://www.siemens.com/simatic-s7-opcua"
STATE_POLL_INTERVAL = 5

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

    async def _subscribe(self, client: asyncua.Client, ns_index: int) -> None:
        subscription = await client.create_subscription(1000, self)
        for node_id in self._config.monitor_nodes:
            node = client.get_node(ua.NodeId(node_id, ns_index))
            try:
                await subscription.subscribe_data_change(node)
            except UaStatusCodeError:
                _logger.exception("Error subscribing to node %s", node_id)
                raise

    async def _poll_status(self, client: asyncua.Client) -> NoReturn:
        server_state = client.get_node(ua.ObjectIds.Server_ServerStatus_State)
        while True:
            await asyncio.sleep(STATE_POLL_INTERVAL)
            await server_state.read_data_value()

    async def _poll_nodes(self, client: asyncua.Client, nsi: int) -> NoReturn:
        polled_nodes = [
            client.get_node(ua.NodeId(node_id, nsi))
            for node_id in self._config.record_nodes
        ]
        while True:
            last_time = time.monotonic()
            values = await client.read_values(polled_nodes)
            for node, value in zip(polled_nodes, values):
                message = OPCDataMessage(node.nodeid.Identifier, value)
                self._influx_writer.put(message)
            elapsed = time.monotonic() - last_time
            await asyncio.sleep(self._config.record_interval - elapsed)

    async def _task(self) -> None:
        client = await self._create_opc_client()

        async with client:
            nsi = await client.get_namespace_index(SIMATIC_NAMESPACE_URI)

            simatic_types_var = await client.nodes.opc_binary.get_child(
                f"{nsi}:SimaticStructures"
            )
            await client.load_type_definitions([simatic_types_var])

            await self._subscribe(client, nsi)

            coros = [
                self._poll_status(client),
                self._poll_nodes(client, nsi),
            ]
            tasks = [asyncio.create_task(coro) for coro in coros]
            try:
                await asyncio.gather(*tasks)
            except BaseException as exc:
                for task in tasks:
                    task.cancel()
                raise exc

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
        message = OPCDataMessage(node_id=node_id, ua_object=val)
        self._centrifugo_proxy_server.record_last_opc_data(message)
        self._frontend_messaging_writer.put(message)

    def before_sleep(self, retry_state: tenacity.RetryCallState) -> None:
        """Callback to be called before sleeping on each task retrying."""
        self.set_status(LinkStatus.Down)
        sleep_time = float("NaN")
        if (next_action := retry_state.next_action) is not None:  # pragma: no branch
            sleep_time = next_action.sleep
        exc_message = "no exception !"
        if (outcome := retry_state.outcome) is not None:  # pragma: no branch
            if (exc := outcome.exception()) is not None:  # pragma: no branch
                exc_message = f"{type(exc).__name__}: {exc}"
        _logger.info(
            "Retrying OPC client task in %s seconds as it raised %s",
            sleep_time,
            exc_message,
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
        await retryer(self._task)
