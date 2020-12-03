import asyncio
import logging
from logging import LogRecord
from typing import Any, Callable, Iterator, Type

import pytest
from _pytest.logging import LogCaptureFixture

from opcua_webhmi_bridge.library import QUEUE_MAXSIZE, AsyncTask, MessageConsumer

LogRecordsType = Callable[[], Iterator[LogRecord]]

_logger = logging.getLogger("test_library")


@pytest.fixture
def log_records(caplog: LogCaptureFixture) -> LogRecordsType:
    def inner() -> Iterator[LogRecord]:
        return filter(lambda r: r.name == _logger.name, caplog.records)

    return inner


@pytest.fixture
def async_task() -> Type[AsyncTask]:
    class AsyncTaskSubclass(AsyncTask):
        logger = _logger
        purpose = "Async task testing"

        async def task(self) -> None:
            pass

    return AsyncTaskSubclass


@pytest.fixture
def message_consumer() -> Type[MessageConsumer[Any]]:
    class MessageConsumerSubclass(MessageConsumer[Any]):
        logger = _logger
        purpose = "Message consumer testing"

        async def task(self) -> None:
            pass

    return MessageConsumerSubclass


class TestAsyncTask:
    def test_instanciates(self, async_task: Type[AsyncTask]) -> None:
        async_task()

    @pytest.mark.filterwarnings("ignore::RuntimeWarning")
    def test_task_scheduled(
        self, async_task: Type[AsyncTask], event_loop: asyncio.AbstractEventLoop
    ) -> None:
        instance = async_task()
        assert len(asyncio.all_tasks(event_loop)) == 0
        instance.run(event_loop)
        assert asyncio.all_tasks(event_loop).pop().get_name() == instance.purpose


class TestMessageConsumer:
    def test_instanciates(self, message_consumer: Type[MessageConsumer[Any]]) -> None:
        message_consumer()

    def test_queue_full(
        self,
        message_consumer: Type[MessageConsumer[Any]],
        log_records: LogRecordsType,
    ) -> None:
        instance = message_consumer()
        for _ in range(QUEUE_MAXSIZE):
            instance.put("message")
        assert not any(r.levelno == logging.ERROR for r in log_records())
        instance.put("overflow")
        last_record = list(log_records())[-1]
        assert last_record.levelno == logging.ERROR
        assert "message queue full" in last_record.message
