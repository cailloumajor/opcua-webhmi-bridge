# pyright: strict

import asyncio
import json
import logging
from typing import Any

import asyncua
from asyncua import ua
from asyncua.common.subscription import SubscriptionItemData

from .config import Config
from .pubsub import Hub

SIMATIC_NAMESPACE_URI = "http://www.siemens.com/simatic-s7-opcua"


class OPCUAEncoder(json.JSONEncoder):
    def default(self, o: Any):
        if hasattr(o, "ua_types"):
            return {elem: getattr(o, elem) for elem, _ in o.ua_types}
        return super().default(o)


class UAClient:
    def __init__(self, config: Config, hub: Hub) -> None:
        self._config = config
        self._hub = hub

    def datachange_notification(  # noqa: U100
        self, node: asyncua.Node, val: ua.ExtensionObject, data: SubscriptionItemData
    ):
        node_id = node.nodeid.Identifier.replace('"', "")
        logging.debug("datachange_notification for %s %s", node, val)
        self._hub.publish(
            json.dumps(
                {"type": "opc_data_change", "node": node_id, "data": val},
                cls=OPCUAEncoder,
            )
        )

    async def task(self) -> None:
        retrying = False
        while True:
            if retrying:
                logging.info(
                    "OPC-UA connection retry in %d seconds...",
                    self._config.opc_retry_delay,
                )
                await asyncio.sleep(self._config.opc_retry_delay)
            retrying = False
            client = asyncua.Client(url=self._config.opc_server_url)
            try:
                async with client:
                    ns = await client.get_namespace_index(SIMATIC_NAMESPACE_URI)
                    sim_types_var = await client.nodes.opc_binary.get_child(
                        f"{ns}:SimaticStructures"
                    )
                    await client.load_type_definitions([sim_types_var])
                    var = client.get_node(f"ns={ns};s={self._config.opc_monitor_node}")
                    subscription = await client.create_subscription(1000, self)
                    await subscription.subscribe_data_change(var)
                    server_state = client.get_node(
                        ua.ObjectIds.Server_ServerStatus_State
                    )
                    while True:
                        await asyncio.sleep(1)
                        await server_state.get_data_value()
            except (OSError, asyncio.TimeoutError) as exc:
                logging.error("OPC-UA client error: %s %s", exc.__class__.__name__, exc)
                retrying = True
