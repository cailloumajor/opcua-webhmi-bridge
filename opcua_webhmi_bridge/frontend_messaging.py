import logging
from typing import Dict

import jwt
from aiohttp import ClientError, ClientSession, ClientTimeout, web

from ._utils import GenericWriter
from .config import MessagingSettings
from .messages import DataChangePayload, LinkStatus, OPCDataChangeMessage, OPCMessage


class FrontendMessagingWriter(GenericWriter[OPCMessage, MessagingSettings]):
    purpose = "Frontend messaging"

    async def _task(self) -> None:
        api_key = self._config.api_key.get_secret_value()
        headers = {"Authorization": f"apikey {api_key}"}
        async with ClientSession(
            headers=headers, raise_for_status=True, timeout=ClientTimeout(total=10)
        ) as session:
            while True:
                message = await self._queue.get()
                command = {
                    "method": "publish",
                    "params": {
                        "channel": message.message_type,
                        "data": message.frontend_data,
                    },
                }
                try:
                    await session.post(self._config.api_url, json=command)
                except ClientError as err:
                    logging.error(
                        "Frontend messaging %s error: %s", command["method"], err
                    )


class BackendServer:
    def __init__(self, config: MessagingSettings) -> None:
        self._config = config
        self._last_opc_data: Dict[str, DataChangePayload] = {}
        self.last_opc_status: LinkStatus = LinkStatus.Down

    def clear_last_opc_data(self) -> None:
        self._last_opc_data = {}

    def record_last_opc_data(self, message: OPCDataChangeMessage) -> None:
        self._last_opc_data[message.node_id] = message.payload

    async def hello(self, request: web.Request) -> web.Response:  # noqa: U100
        token = jwt.encode({"sub": ""}, self._config.secret_key.get_secret_value())
        last_opc_data = [
            {"node_id": k, "payload": v} for k, v in self._last_opc_data.items()
        ]
        resp_data = {
            "token": token.decode(),
            "last_opc_data": last_opc_data,
            "last_opc_status": self.last_opc_status,
        }
        return web.json_response(resp_data)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/centrifuge/hello", self.hello)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self._config.backend_host, self._config.backend_port)
        await site.start()
        logging.info("Backend HTTP server started")
