# pyright: strict

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import TYPE_CHECKING, Set, TypeVar

_T = TypeVar("_T")

if TYPE_CHECKING:
    BaseQueue = asyncio.Queue[_T]
else:
    BaseQueue = asyncio.Queue


class SingleElemOverwriteQueue(BaseQueue):
    """A subclass of asyncio.Queue.
    It stores only one element and overwrites it when putting.
    """

    def _init(self, maxsize: int):  # noqa: U100
        self._queue = None

    def _put(self, item: _T):
        self._queue = item

    def _get(self) -> _T:
        item = self._queue
        self._queue = None
        return item


class Hub:
    def __init__(self) -> None:
        self._subscriptions: Set[SingleElemOverwriteQueue[str]] = set()
        self._last_message = None

    def _add_subscription(self, subscription: SingleElemOverwriteQueue[str]):
        self._subscriptions.add(subscription)
        if self._last_message:
            subscription.put_nowait(self._last_message)

    def _remove_subscription(self, subscription: SingleElemOverwriteQueue[str]):
        self._subscriptions.remove(subscription)

    def publish(self, message: str):
        self._last_message = message
        for queue in self._subscriptions:
            queue.put_nowait(message)

    @contextmanager
    def subscribe(self):
        queue: SingleElemOverwriteQueue[str] = SingleElemOverwriteQueue()
        self._add_subscription(queue)
        try:
            yield queue
        finally:
            self._remove_subscription(queue)
