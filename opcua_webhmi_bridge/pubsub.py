import asyncio
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, Optional, Set, TypeVar

_T = TypeVar("_T")

if TYPE_CHECKING:
    BaseQueue = asyncio.Queue
else:

    class FakeGenericMeta(type):
        def __getitem__(cls, item):  # noqa: U100
            return cls

    class BaseQueue(asyncio.Queue, metaclass=FakeGenericMeta):
        pass


class SingleElemOverwriteQueue(BaseQueue[_T]):
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


class _Hub:
    def __init__(self) -> None:
        self._subscribers: Set[SingleElemOverwriteQueue[str]] = set()
        self._last_message = ""

    def publish(self, message: str, retain: bool = False) -> None:
        if retain:
            self._last_message = message
        for queue in self._subscribers:
            queue.put_nowait(message)

    @contextmanager
    def subscribe(self) -> Iterator[SingleElemOverwriteQueue[str]]:
        queue: SingleElemOverwriteQueue[str] = SingleElemOverwriteQueue()
        self._subscribers.add(queue)
        if self._last_message:
            asyncio.create_task(self.put_last_message(queue))
        try:
            yield queue
        finally:
            self._subscribers.remove(queue)

    async def put_last_message(self, queue: SingleElemOverwriteQueue[str]) -> None:
        await asyncio.sleep(1)
        await queue.put(self._last_message)


hub = _Hub()
