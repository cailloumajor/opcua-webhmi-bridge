from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Iterator, Optional, Set, TypeVar

_T = TypeVar("_T")


class SingleElemOverwriteQueue(asyncio.Queue[_T]):
    """A subclass of asyncio.Queue.
    It stores only one element and overwrites it when putting.
    """

    def _init(self, maxsize: int) -> None:  # noqa: U100
        self._queue: Optional[_T] = None

    def _put(self, item: _T) -> None:
        self._queue = item

    def _get(self) -> _T:
        # The assert line is here to satisfy mypy type check.
        # Ensuring that self._queue is not None is already done in the parent class.
        assert self._queue is not None  # nosec
        item = self._queue
        self._queue = None
        return item


class Hub:
    def __init__(self) -> None:
        self._subscriptions: Set[SingleElemOverwriteQueue[str]] = set()
        self._last_message = ""

    def _add_subscription(self, subscription: SingleElemOverwriteQueue[str]) -> None:
        self._subscriptions.add(subscription)
        if self._last_message:
            subscription.put_nowait(self._last_message)

    def _remove_subscription(self, subscription: SingleElemOverwriteQueue[str]) -> None:
        self._subscriptions.remove(subscription)

    def publish(self, message: str) -> None:
        self._last_message = message
        for queue in self._subscriptions:
            queue.put_nowait(message)

    @contextmanager
    def subscribe(self) -> Iterator[SingleElemOverwriteQueue[str]]:
        queue: SingleElemOverwriteQueue[str] = SingleElemOverwriteQueue()
        self._add_subscription(queue)
        try:
            yield queue
        finally:
            self._remove_subscription(queue)
