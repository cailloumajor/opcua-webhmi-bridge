"""Utilities used across all the package."""

import asyncio
from abc import ABC, abstractmethod
from asyncio.events import AbstractEventLoop
from logging import Logger
from typing import Generic, TypeVar

from .messages import BaseMessage

MT = TypeVar("MT", bound=BaseMessage)  # Generic message type

QUEUE_MAXSIZE = 10


class AsyncTask(ABC):
    """Asynchronous task base class.

    Class attributes:
        logger: The logger to be used by instances of subclasses.
        purpose: A string describing the purpose of subclasses.
    """

    @property
    @abstractmethod
    def logger(self) -> Logger:
        """Ensures subclasses overrides this attribute."""
        raise NotImplementedError

    @property
    @abstractmethod
    def purpose(self) -> str:
        """Ensures subclasses overrides this attribute."""
        raise NotImplementedError

    @abstractmethod
    async def task(self) -> None:
        """Asynchronous task. Must be overriden by subclasses."""
        raise NotImplementedError

    def run(self, loop: AbstractEventLoop) -> None:
        """Runs the asynchronous task.

        Args:
            loop: The event loop on which to schedule the task.
        """
        self.logger.info("%s task running", self.purpose)
        loop.create_task(self.task(), name=self.purpose)


class MessageConsumer(AsyncTask, Generic[MT]):
    """Message consumer base class. Inherits from asynchronous base class."""

    def __init__(self) -> None:
        """Initialize message consumer (create the queue)."""
        self._queue: asyncio.Queue[MT] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)

    def put(self, message: MT) -> None:
        """Enqueue a message.

        Args:
            message: The message to enqueue.
        """
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            self.logger.error("%s message queue full, message discarded", self.purpose)
