"""Management of messaging changes to frontend application."""

import asyncio
import logging
from json.decoder import JSONDecodeError
from typing import Dict, Union

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .config import CentrifugoSettings
from .library import AsyncTask, MessageConsumer
from .messages import (
    HeartBeatMessage,
    LinkStatus,
    OPCDataChangeMessage,
    OPCStatusMessage,
)

OPCMessage = Union[OPCDataChangeMessage, OPCStatusMessage]

HEARTBEAT_TIMEOUT = 5


_logger = logging.getLogger(__name__)


class FrontendMessagingWriter(MessageConsumer[OPCMessage]):
    """Handles signalization to frontend."""

    logger = _logger
    purpose = "Frontend messaging publisher"

    def __init__(self, config: CentrifugoSettings):
        """Initializes frontend signalization.

        Args:
            config: Centrifugo related configuration options.
        """
        super().__init__()
        self._config = config

    async def task(self) -> None:
        """Implements frontend signalization asynchronous task."""
        api_key = self._config.api_key.get_secret_value()
        headers = {"Authorization": f"apikey {api_key}"}
        async with ClientSession(
            headers=headers, timeout=ClientTimeout(total=10)
        ) as session:
            while True:
                message: Union[OPCMessage, HeartBeatMessage]
                try:
                    message = await asyncio.wait_for(
                        self._queue.get(), timeout=HEARTBEAT_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    message = HeartBeatMessage()
                command = {
                    "method": "publish",
                    "params": {
                        "channel": message.message_type.value,
                        "data": message.frontend_data,
                    },
                }
                try:
                    async with session.post(self._config.api_url, json=command) as resp:
                        resp.raise_for_status()
                        resp_data = await resp.json()
                        if (error := resp_data.get("error")) is not None:
                            _logger.error(
                                "%s - Centrifugo API error: %s %s",
                                self.purpose,
                                error["code"],
                                error["message"],
                            )
                except ClientError as err:
                    _logger.error("%s error: %s", self.purpose, err)


class CentrifugoProxyServer(AsyncTask):
    """Centrifugo HTTP proxy server."""

    logger = _logger
    purpose = "Centrifugo proxy server"

    def __init__(
        self, config: CentrifugoSettings, messaging_writer: FrontendMessagingWriter
    ) -> None:
        """Initialize Centrifugo proxy server instance.

        Args:
            config: Centrifugo related configuration options.
            messaging_writer: Instance of frontend signalization task.
        """
        self._config = config
        self._messaging_writer = messaging_writer
        self._last_opc_data: Dict[str, OPCDataChangeMessage] = {}
        self.last_opc_status = OPCStatusMessage(LinkStatus.Down)

    def clear_last_opc_data(self) -> None:
        """Clears the record of last OPC-UA data received."""
        self._last_opc_data = {}

    def record_last_opc_data(self, message: OPCDataChangeMessage) -> None:
        """Records the last OPC-UA data received for each node ID.

        Args:
            message: The message to add to the record.
        """
        self._last_opc_data[message.node_id] = message

    async def centrifugo_subscribe(self, request: web.Request) -> web.Response:
        """Handle Centrifugo subscription requests."""
        try:
            context = await request.json()
            channel = context.get("channel")
        except JSONDecodeError:
            raise web.HTTPInternalServerError(reason="JSON decode error")
        except AttributeError:
            raise web.HTTPBadRequest(reason="Bad request format")
        if channel is None:
            raise web.HTTPBadRequest(reason="Missing channel field")
        elif channel == OPCDataChangeMessage.message_type:
            for message in self._last_opc_data.values():
                self._messaging_writer.put(message)
        elif channel == OPCStatusMessage.message_type:
            self._messaging_writer.put(self.last_opc_status)
        else:
            raise web.HTTPBadRequest(reason="Unknown channel")
        return web.json_response({"result": {}})

    async def task(self) -> None:
        """Implements Centrifugo proxy asynchronous task."""
        app = web.Application()
        app.router.add_post("/centrifugo/subscribe", self.centrifugo_subscribe)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, self._config.proxy_host, self._config.proxy_port)
            await site.start()
            _logger.info("Centrifugo proxy server started")
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()
