import asyncio
import logging

import websockets

from .config import config
from .pubsub import hub


async def _handler(  # noqa: U100
    websocket: websockets.WebSocketServerProtocol, path: str
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


start_server = websockets.serve(_handler, config.websocket_host, config.websocket_port)
