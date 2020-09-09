import logging

from aiohttp import ClientError, ClientSession, ClientTimeout

from ._utils import GenericWriter
from .config import MessagingSettings
from .messages import OPCMessage


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
