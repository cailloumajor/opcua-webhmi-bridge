#!/usr/bin/env python3.8
# pyright: strict

import asyncio
import functools
import json
import logging
import signal
from argparse import RawDescriptionHelpFormatter
from typing import Any, Dict, Optional

import asyncua
import websockets
from asyncua import ua
from asyncua.common.subscription import SubscriptionItemData
from opcua_webhmi_bridge.config import Config
from opcua_webhmi_bridge.pubsub import Hub
from tap import Tap
from websockets import WebSocketServerProtocol

SIMATIC_NAMESPACE_URI = "http://www.siemens.com/simatic-s7-opcua"


class OPCUAEncoder(json.JSONEncoder):
    def default(self, o: Any):
        if hasattr(o, "ua_types"):
            return {elem: getattr(o, elem) for elem, _ in o.ua_types}
        return super().default(o)


class OPCUASubscriptionHandler:
    def __init__(self, hub: Hub) -> None:
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


async def opcua_task(config: Config, hub: Hub) -> None:
    retrying = False
    while True:
        if retrying:
            logging.info(
                "OPC-UA connection retry in %d seconds...", config.opc_retry_delay
            )
            await asyncio.sleep(config.opc_retry_delay)
        retrying = False
        client = asyncua.Client(url=config.opc_server_url)
        try:
            async with client:
                ns = await client.get_namespace_index(SIMATIC_NAMESPACE_URI)
                sim_types_var = await client.nodes.opc_binary.get_child(
                    f"{ns}:SimaticStructures"
                )
                await client.load_type_definitions([sim_types_var])
                var = client.get_node(f"ns={ns};s={config.opc_monitor_node}")
                subscription = await client.create_subscription(
                    1000, OPCUASubscriptionHandler(hub)
                )
                await subscription.subscribe_data_change(var)
                server_state = client.get_node(ua.ObjectIds.Server_ServerStatus_State)
                while True:
                    await asyncio.sleep(1)
                    await server_state.get_data_value()
        except (OSError, asyncio.TimeoutError) as exc:
            logging.error("OPC-UA client error: %s %s", exc.__class__.__name__, exc)
            retrying = True


async def websockets_handler(  # noqa: U100
    websocket: WebSocketServerProtocol, path: str, hub: Hub
) -> None:
    client_address = websocket.remote_address[0]
    logging.info("WebSocket client connected from %s", client_address)
    with hub.subscribe() as queue:
        task_msg_wait = asyncio.create_task(queue.get())
        task_client_disconnect = asyncio.create_task(websocket.wait_closed())
        while True:
            done, pending = await asyncio.wait(
                [task_msg_wait, task_client_disconnect],
                return_when=asyncio.FIRST_COMPLETED,
            )
            must_shutdown = False
            for task in done:
                if task is task_msg_wait:
                    msg = task.result()
                    await websocket.send(str(msg))
                    task_msg_wait = asyncio.create_task(queue.get())
                elif task is task_client_disconnect:
                    logging.info(
                        "WebSocket client disconnected from %s", client_address
                    )
                    must_shutdown = True
            if must_shutdown:
                for task in pending:
                    task.cancel()
                    await task
                break


async def shutdown(
    loop: asyncio.AbstractEventLoop, sig: Optional[signal.Signals] = None
) -> None:
    """Cleanup tasks tied to the service's shutdown"""
    if sig:
        logging.info("Received exit signal %s", sig.name)
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    for task in tasks:
        task.cancel()

    logging.info("Waiting for %s outstanding tasks to finish...", len(tasks))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if not isinstance(result, asyncio.CancelledError) and isinstance(
            result, Exception
        ):
            logging.error("Exception occured during shutdown: %s", result)
    loop.stop()


def handle_exception(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]):
    # context["message"] will always be there; but context["exception"] may not
    try:
        exc: Exception = context["exception"]
    except KeyError:
        logging.error("Caught exception: %s", context["message"])
    else:
        logging.error("Caught exception %s: %s", exc.__class__.__name__, exc)
    logging.info("Shutting down...")
    asyncio.create_task(shutdown(loop))


def main():
    class ArgumentParser(Tap):
        verbose: bool = False

    parser = ArgumentParser(
        description="Bridge between OPC-UA server and web-based HMI",
        epilog=f"Environment variables:\n{Config.generate_help()}",
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    config = Config()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s:%(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )
    if not args.verbose:
        for logger in [
            "asyncua.common.subscription",
            "asyncua.client.ua_client.UASocketProtocol",
        ]:
            logging.getLogger(logger).setLevel(logging.ERROR)

    hub = Hub()
    bound_ws_handler = functools.partial(websockets_handler, hub=hub)
    start_ws_server = websockets.serve(
        bound_ws_handler, config.websocket_host, config.websocket_port
    )
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    for s in signals:
        loop.add_signal_handler(
            s, lambda s=s: asyncio.create_task(shutdown(loop, sig=s))
        )
    loop.set_exception_handler(handle_exception)

    try:
        loop.run_until_complete(start_ws_server)
        loop.create_task(opcua_task(config, hub))
        loop.run_forever()
    finally:
        loop.close()
        logging.info("Shutdown successfull")


if __name__ == "__main__":
    main()
