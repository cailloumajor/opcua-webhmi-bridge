"""Management of messaging changes to frontend application."""

import asyncio
import logging
import re
from json.decoder import JSONDecodeError
from typing import Dict, Union

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .config import CentrifugoSettings
from .library import AsyncTask, MessageConsumer
from .messages import (
    PROXIED_CHANNEL_NAMESPACE,
    HeartBeatMessage,
    LinkStatus,
    MessageType,
    OPCDataMessage,
    OPCStatusMessage,
)

OPCMessage = Union[OPCDataMessage, OPCStatusMessage]

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
                        "channel": message.message_type.centrifugo_channel,
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
        self._last_opc_data: Dict[str, OPCDataMessage] = {}
        self.last_opc_status = OPCStatusMessage(LinkStatus.Down)

    def clear_last_opc_data(self) -> None:
        """Clears the record of last OPC-UA data received."""
        self._last_opc_data = {}

    def record_last_opc_data(self, message: OPCDataMessage) -> None:
        """Records the last OPC-UA data received for each node ID.

        Args:
            message: The message to add to the record.
        """
        self._last_opc_data[message.node_id] = message

    async def centrifugo_subscribe(self, request: web.Request) -> web.Response:
        """Handle Centrifugo subscription requests."""

        def _error(code: int, message: str) -> web.Response:
            return web.json_response({"error": {"code": code, "message": message}})

        try:
            context = await request.json()
            channel = context.get("channel")
        except JSONDecodeError:
            raise web.HTTPInternalServerError(reason="JSON decode error")
        except AttributeError:
            raise web.HTTPBadRequest(reason="Bad request format")
        if channel is None:
            return _error(1000, "Missing channel field")
        else:
            try:
                channel = re.sub(rf"^{PROXIED_CHANNEL_NAMESPACE}:", "", channel)
            except TypeError:
                raise web.HTTPBadRequest(reason="Channel must be a string")
        if channel == MessageType.OPC_DATA:
            for message in self._last_opc_data.values():
                self._messaging_writer.put(message)
        elif channel == MessageType.OPC_STATUS:
            self._messaging_writer.put(self.last_opc_status)
        try:
            MessageType(channel)
        except ValueError:
            return _error(1001, "Unknown channel")
        return web.json_response({"result": {}})

    async def task(self) -> None:
        """Implements Centrifugo proxy asynchronous task."""
        app = web.Application()
        app.router.add_post("/centrifugo/subscribe", self.centrifugo_subscribe)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            site = web.TCPSite(runner, None, self._config.proxy_port)
            await site.start()
            _logger.info("Centrifugo proxy server started")
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()
