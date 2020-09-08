import aiohttp

from ._utils import GenericWriter
from .config import MessagingSettings
from .opcua import OPCMessage


class FrontendMessagingWriter(GenericWriter[OPCMessage, MessagingSettings]):
    purpose = "Frontend messaging"

    async def _task(self) -> None:
        headers = {"Authorization": f"apikey {self._config.api_key}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            while True:
                message = await self._queue.get()
                command = {
                    "method": "publish",
                    "params": {"channel": "opcua", "data": message.asdict()},
                }
                await session.post(self._config.centrifugo_url, json=command)
