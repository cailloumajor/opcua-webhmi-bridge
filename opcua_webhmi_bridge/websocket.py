import asyncio
import logging

import websockets

from .config import Config
from .pubsub import Hub


class WebsocketServer:
    def __init__(self, config: Config, hub: Hub):
        self._config = config
        self._hub = hub

    async def _handler(  # noqa: U100
        self, websocket: websockets.WebSocketServerProtocol, path: str
    ) -> None:
        client_address = websocket.remote_address[0]
        logging.info("WebSocket client connected from %s", client_address)
        with self._hub.subscribe() as queue:
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

    @property
    def start_server(self) -> websockets.server.Serve:
        return websockets.serve(
            self._handler, self._config.websocket_host, self._config.websocket_port
        )
