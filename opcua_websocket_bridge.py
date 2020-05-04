#!/usr/bin/env python3.8
# pyright: strict
from __future__ import annotations

import asyncio
import functools
import json
import logging
import signal
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, TypeVar

import asyncua
import websockets
from asyncua import ua
from asyncua.common.subscription import SubscriptionItemData
from websockets import WebSocketServerProtocol

_T = TypeVar("_T")

if TYPE_CHECKING:
    BaseQueue = asyncio.Queue[_T]
else:
    BaseQueue = asyncio.Queue

OPC_RETRY_DELAY = 5
SIEMENS_NAMESPACE_URI = "http://www.siemens.com/simatic-s7-opcua"


class SingleElemOverwriteQueue(BaseQueue):
    """A subclass of asyncio.Queue.
    It stores only one element and overwrites it when putting.
    """

    def _init(self, maxsize: int):
        self._queue = None

    def _put(self, item: _T):
        self._queue = item

    def _get(self) -> _T:
        item = self._queue
        self._queue = None
        return item


class Hub:
    def __init__(self) -> None:
        self.subscriptions: Set[SingleElemOverwriteQueue[str]] = set()

    def publish(self, message: str):
        for queue in self.subscriptions:
            queue.put_nowait(message)


class OPCUAEncoder(json.JSONEncoder):
    def default(self, o: Any):
        if hasattr(o, "ua_types"):
            return {elem: getattr(o, elem) for elem, _ in o.ua_types}
        return super().default(o)


class OPCUASubscriptionHandler:
    def __init__(self, hub: Hub) -> None:
        self._hub = hub

    def datachange_notification(
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


@contextmanager
def subscription(hub: Hub):
    queue: SingleElemOverwriteQueue[str] = SingleElemOverwriteQueue()
    hub.subscriptions.add(queue)
    try:
        yield queue
    finally:
        hub.subscriptions.remove(queue)


async def opcua_task(hub: Hub) -> None:
    retrying = False
    while True:
        if retrying:
            logging.info("Connection retry in %d seconds...", OPC_RETRY_DELAY)
            await asyncio.sleep(OPC_RETRY_DELAY)
        retrying = False
        connected = False
        client = asyncua.Client(url="opc.tcp://192.168.50.1:4840/")
        client.session_timeout = 30000
        try:
            await client.connect()
            connected = True
            ns = await client.get_namespace_index(SIEMENS_NAMESPACE_URI)
            sim_types_var = client.get_node(ua.NodeId(6001, ns))
            await client.load_type_definitions([sim_types_var])
            var = client.get_node(ua.NodeId('"dbLineSupervision"."machine"', ns))
            subscription = await client.create_subscription(
                1000, OPCUASubscriptionHandler(hub)
            )
            await subscription.subscribe_data_change(var)
            server_state = client.get_node(ua.ObjectIds.Server_ServerStatus_State)
            while True:
                await asyncio.sleep(1)
                await server_state.get_data_value()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.error("OPC-UA client error: %s %s", exc.__class__.__name__, exc)
            retrying = True
        finally:
            if connected:
                await client.disconnect()


async def websockets_handler(
    websocket: WebSocketServerProtocol, path: str, hub: Hub
) -> None:
    client_address = websocket.remote_address[0]
    logging.info("WebSocket client connected from %s", client_address)
    with subscription(hub) as queue:
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
    logging.basicConfig(level=logging.INFO)

    hub = Hub()
    bound_ws_handler = functools.partial(websockets_handler, hub=hub)
    start_ws_server = websockets.serve(bound_ws_handler, "0.0.0.0", 3000)
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
        loop.create_task(opcua_task(hub))
        loop.run_forever()
    finally:
        loop.close()
        logging.info("Shutdown successfull")


if __name__ == "__main__":
    main()
