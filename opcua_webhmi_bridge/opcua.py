import asyncio
import json
import logging
from dataclasses import InitVar, asdict, dataclass, field
from typing import Any, Dict, Union

import asyncua
import tenacity
from asyncua import ua
from asyncua.common.subscription import SubscriptionItemData

from .config import OPCSettings
from .frontend_messaging import FrontendMessagingWriter
from .influxdb import InfluxDBWriter

SIMATIC_NAMESPACE_URI = "http://www.siemens.com/simatic-s7-opcua"


class OPCUAEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if hasattr(o, "ua_types"):
            return {elem: getattr(o, elem) for elem, _ in o.ua_types}
        return super().default(o)


@dataclass
class BaseMessage:
    message_type: str = field(init=False)

    def asdict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OPCDataChangeMessage(BaseMessage):
    message_type = "opc_data_change"
    node_id: str
    payload: Dict[str, Any] = field(init=False)
    ua_object: InitVar[ua.ExtensionObject]

    def __post_init__(self, ua_object: ua.ExtensionObject) -> None:
        self.payload = json.loads(json.dumps(ua_object, cls=OPCUAEncoder))


@dataclass
class OPCStatusMessage(BaseMessage):
    message_type = "opc_status"
    payload: bool


OPCMessage = Union[OPCDataChangeMessage, OPCStatusMessage]


class OPCUAClient:
    def __init__(
        self,
        config: OPCSettings,
        influx_writer: InfluxDBWriter,
        frontend_messaging_writer: FrontendMessagingWriter,
    ):
        self._config = config
        self._frontend_messaging_writer = frontend_messaging_writer
        self._influx_writer = influx_writer
        self._status = False

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

    def datachange_notification(  # noqa: U100
        self, node: asyncua.Node, val: ua.ExtensionObject, data: SubscriptionItemData
    ) -> None:
        node_id = node.nodeid.Identifier
        logging.debug("datachange_notification for %s %s", node_id, val)
        self._status = True
        message = OPCDataChangeMessage(node_id=node_id, ua_object=val)
        self._frontend_messaging_writer.put(message)
        if node_id in self._config.record_nodes:
            self._influx_writer.put(message)

    def before_sleep(self, retry_state: tenacity.RetryCallState) -> None:
        if self._status:
            self._frontend_messaging_writer.put(OPCStatusMessage(payload=False))
        self._status = False
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
