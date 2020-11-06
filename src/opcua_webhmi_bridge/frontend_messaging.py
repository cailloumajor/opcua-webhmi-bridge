import asyncio
import logging
from typing import Dict, Union

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from ._library import AsyncTask, MessageConsumer
from .config import CentrifugoSettings
from .messages import (
    HeartBeatMessage,
    LinkStatus,
    OPCDataChangeMessage,
    OPCStatusMessage,
)

OPCMessage = Union[OPCDataChangeMessage, OPCStatusMessage]


class FrontendMessagingWriter(MessageConsumer[OPCMessage]):
    purpose = "Frontend messaging publisher"

    def __init__(self, config: CentrifugoSettings):
        super().__init__()
        self._config = config

    async def task(self) -> None:
        api_key = self._config.api_key.get_secret_value()
        headers = {"Authorization": f"apikey {api_key}"}
        async with ClientSession(
            headers=headers, raise_for_status=True, timeout=ClientTimeout(total=10)
        ) as session:
            while True:
                message: Union[OPCMessage, HeartBeatMessage]
                try:
                    message = await asyncio.wait_for(self._queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    message = HeartBeatMessage()
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
                    logging.error("Frontend messaging publish error: %s", err)


class CentrifugoProxyServer(AsyncTask):
    purpose = "Centrifugo proxy server"

    def __init__(
        self, config: CentrifugoSettings, messaging_writer: FrontendMessagingWriter
    ) -> None:
        self._config = config
        self._messaging_writer = messaging_writer
        self._last_opc_data: Dict[str, OPCDataChangeMessage] = {}
        self.last_opc_status = OPCStatusMessage(LinkStatus.Down)

    def clear_last_opc_data(self) -> None:
        self._last_opc_data = {}

    def record_last_opc_data(self, message: OPCDataChangeMessage) -> None:
        self._last_opc_data[message.node_id] = message

    async def centrifugo_subscribe(self, request: web.Request) -> web.Response:
        context = await request.json()
        channel = context["channel"]
        if channel == OPCDataChangeMessage.message_type:
            for message in self._last_opc_data.values():
                self._messaging_writer.put(message)
        elif channel == OPCStatusMessage.message_type:
            self._messaging_writer.put(self.last_opc_status)
        return web.json_response({"result": {}})

    async def task(self) -> None:
        app = web.Application()
        app.router.add_post("/centrifugo/subscribe", self.centrifugo_subscribe)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, self._config.proxy_host, self._config.proxy_port)
            await site.start()
            logging.info("Centrifugo proxy server started")
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()
