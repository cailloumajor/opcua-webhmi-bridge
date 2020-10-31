import asyncio
from abc import ABC, abstractmethod
from asyncio.events import AbstractEventLoop
from logging import Logger
from typing import Generic, TypeVar

from .messages import BaseMessage

MT = TypeVar("MT", bound=BaseMessage)  # Generic message type

QUEUE_MAXSIZE = 10


class AsyncTask(ABC):
    @property
    @abstractmethod
    def logger(self) -> Logger:
        raise NotImplementedError

    @property
    @abstractmethod
    def purpose(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def task(self) -> None:
        raise NotImplementedError

    def run(self, loop: AbstractEventLoop) -> None:
        self.logger.info("%s task running", self.purpose)
        loop.create_task(self.task(), name=self.purpose)


class MessageConsumer(AsyncTask, Generic[MT]):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[MT] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)

    def put(self, message: MT) -> None:
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            self.logger.error("%s message queue full, message discarded", self.purpose)