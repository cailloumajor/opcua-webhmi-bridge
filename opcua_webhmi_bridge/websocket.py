import asyncio
import logging
from asyncio import Task
from typing import List, Tuple, Union

import websockets

from .config import WebSocketSettings
from .pubsub import OPCMessage, hub


def get_client_address(
    websocket: websockets.WebSocketServerProtocol,
) -> Tuple[str, str]:
    for header in ("X-Real-Ip", "X-Forwarded-For"):
        try:
            return (websocket.request_headers[header], header)
        except KeyError:
            pass
    return (websocket.remote_address[0], "socket peer name")


async def _handler(  # noqa: U100
    websocket: websockets.WebSocketServerProtocol, path: str
) -> None:
    client_address, address_from = get_client_address(websocket)
    logging.info(
        "WebSocket client connected from %s (%s)", client_address, address_from
    )
    with hub.subscribe() as queue:
        task_msg_wait = asyncio.create_task(queue.get())
        task_client_disconnect = asyncio.create_task(websocket.wait_closed())
        while True:
            futures: List[Union[Task[OPCMessage], Task[None]]] = [
                task_msg_wait,
                task_client_disconnect,
            ]
            done, pending = await asyncio.wait(
                futures, return_when=asyncio.FIRST_COMPLETED,
            )
            must_stop = False
            for done_task in done:
                if done_task is task_msg_wait:
                    msg = str(done_task.result())
                    await websocket.send(msg)
                    task_msg_wait = asyncio.create_task(queue.get())
                elif done_task is task_client_disconnect:
                    logging.info(
                        "WebSocket client disconnected from %s (%s)",
                        client_address,
                        address_from,
                    )
                    must_stop = True
            if must_stop:
                for pending_task in pending:
                    pending_task.cancel()
                    try:
                        await pending_task
                    except asyncio.CancelledError:
                        pass
                break


def start_server(config: WebSocketSettings) -> websockets.server.Serve:
    return websockets.serve(_handler, config.host, config.port)
