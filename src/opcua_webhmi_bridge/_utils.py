import logging
from abc import ABC, abstractmethod
from asyncio import Queue, QueueFull
from typing import Generic, TypeVar

from pydantic import BaseSettings

from .messages import BaseMessage

MT = TypeVar("MT", bound=BaseMessage)  # Generic message type
CT = TypeVar("CT", bound=BaseSettings)  # Generic configuration type


class GenericWriter(ABC, Generic[MT, CT]):
    def __init__(self, config: CT):
        self._config = config
        self._queue: Queue[MT] = Queue(maxsize=10)

    def put(self, message: MT) -> None:
        try:
            self._queue.put_nowait(message)
        except QueueFull:
            logging.error("%s message queue is full, message discarded", self.purpose)

    async def run_task(self) -> None:
        logging.info("%s writer task running", self.purpose)
        await self._task()

    @property
    @abstractmethod
    def purpose(self) -> str:
        ...

    @abstractmethod
    async def _task(self) -> None:
        ...
