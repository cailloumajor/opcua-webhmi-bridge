import asyncio
import json
import logging
from typing import Any

import asyncua
import tenacity
from asyncua import ua
from asyncua.common.subscription import SubscriptionItemData

from .config import config
from .pubsub import hub

SIMATIC_NAMESPACE_URI = "http://www.siemens.com/simatic-s7-opcua"


class OPCUAEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if hasattr(o, "ua_types"):
            return {elem: getattr(o, elem) for elem, _ in o.ua_types}
        return super().default(o)


class _Client:
    def __init__(self) -> None:
        self._status = False

    async def _task(self) -> None:
        client = asyncua.Client(url=config.opc_server_url)
        async with client:
            ns = await client.get_namespace_index(SIMATIC_NAMESPACE_URI)

            sim_types_var = await client.nodes.opc_binary.get_child(
                f"{ns}:SimaticStructures"
            )
            await client.load_type_definitions([sim_types_var])

            var = client.get_node(f"ns={ns};s={config.opc_monitor_node}")
            subscription = await client.create_subscription(1000, self)
            await subscription.subscribe_data_change(var)

            server_state = client.get_node(ua.ObjectIds.Server_ServerStatus_State)

            while True:
                await asyncio.sleep(1)
                await server_state.read_data_value()

    def datachange_notification(  # noqa: U100
        self, node: asyncua.Node, val: ua.ExtensionObject, data: SubscriptionItemData
    ) -> None:
        node_id = node.nodeid.Identifier.replace('"', "")
        logging.debug("datachange_notification for %s %s", node, val)
        self._status = True
        hub.publish(
            json.dumps(
                {"type": "opc_data_change", "node": node_id, "data": val},
                cls=OPCUAEncoder,
            ),
            retain=True,
        )

    def before_sleep(self, retry_state: tenacity.RetryCallState) -> None:
        if self._status:
            hub.publish(json.dumps({"type": "opc_status", "data": False}))
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
            wait=tenacity.wait_fixed(config.opc_retry_delay),
            retry=(
                tenacity.retry_if_exception_type(OSError)
                | tenacity.retry_if_exception_type(asyncio.TimeoutError)
            ),
            before_sleep=self.before_sleep,
        )
        await retryer.call(self._task)


client = _Client()
