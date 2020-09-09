import logging
from abc import ABC, abstractmethod
from asyncio import Queue, QueueFull
from typing import Generic, TypeVar

from pydantic import BaseSettings

T = TypeVar("T")
CT = TypeVar("CT", bound=BaseSettings)


class GenericWriter(ABC, Generic[T, CT]):
    def __init__(self, config: CT):
        self._config = config
        self._queue: Queue[T] = Queue(maxsize=10)

    def put(self, message: T) -> None:
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
