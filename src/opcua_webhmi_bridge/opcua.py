import asyncio
import logging

import asyncua
import tenacity
from asyncua import ua
from asyncua.common.subscription import SubscriptionItemData

from .config import OPCSettings
from .frontend_messaging import CentrifugoProxyServer, FrontendMessagingWriter
from .influxdb import InfluxDBWriter
from .messages import LinkStatus, OPCDataChangeMessage, OPCStatusMessage

SIMATIC_NAMESPACE_URI = "http://www.siemens.com/simatic-s7-opcua"


class OPCUAClient:
    def __init__(
        self,
        config: OPCSettings,
        centrifugo_proxy_server: CentrifugoProxyServer,
        influx_writer: InfluxDBWriter,
        frontend_messaging_writer: FrontendMessagingWriter,
    ):
        self._config = config
        self._centrifugo_proxy_server = centrifugo_proxy_server
        self._frontend_messaging_writer = frontend_messaging_writer
        self._influx_writer = influx_writer
        self._status = LinkStatus.Down

    async def _task(self) -> None:
        client = asyncua.Client(url=self._config.server_url)
        async with client:
            ns = await client.get_namespace_index(SIMATIC_NAMESPACE_URI)

            simatic_types_var = await client.nodes.opc_binary.get_child(
                f"{ns}:SimaticStructures"
            )
            await client.load_type_definitions([simatic_types_var])

            sub_nodes = list(
                set(self._config.monitor_nodes + self._config.record_nodes)
            )
            sub_nodes = [
                client.get_node(f"ns={ns};s={node_id}") for node_id in sub_nodes
            ]
            subscription = await client.create_subscription(1000, self)
            sub_results = await subscription.subscribe_data_change(sub_nodes)
            for index, result in enumerate(sub_results):
                if isinstance(result, ua.StatusCode):
                    logging.error("Error subscribing to node %s", sub_nodes[index])
                    result.check()  # Raise the exception

            server_state = client.get_node(ua.ObjectIds.Server_ServerStatus_State)

            while True:
                await asyncio.sleep(5)
                await server_state.read_data_value()

    def set_status(self, status: LinkStatus) -> None:
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
        val: ua.ExtensionObject,
        data: SubscriptionItemData,  # noqa: U100
    ) -> None:
        node_id = node.nodeid.Identifier
        logging.debug("datachange_notification for %s %s", node_id, val)
        self.set_status(LinkStatus.Up)
        message = OPCDataChangeMessage(node_id=node_id, ua_object=val)
        self._centrifugo_proxy_server.record_last_opc_data(message)
        self._frontend_messaging_writer.put(message)
        if node_id in self._config.record_nodes:
            self._influx_writer.put(message)

    def before_sleep(self, retry_state: tenacity.RetryCallState) -> None:
        self.set_status(LinkStatus.Down)
        exc = retry_state.outcome.exception()  # type: ignore
        logging.info(
            "Retrying OPC client task in %s seconds as it raised %s: %s",
            retry_state.next_action.sleep,  # type: ignore
            type(exc).__name__,
            exc,
        )

    async def retrying_task(self) -> None:
        retryer = tenacity.AsyncRetrying(  # type: ignore
            wait=tenacity.wait_fixed(self._config.retry_delay),
            retry=(
                tenacity.retry_if_exception_type(OSError)
                | tenacity.retry_if_exception_type(asyncio.TimeoutError)
            ),
            before_sleep=self.before_sleep,
        )
        await retryer.call(self._task)
