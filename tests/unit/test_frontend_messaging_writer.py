import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Union

import pytest
from pytest import LogCaptureFixture
from pytest_httpserver import HTTPServer
from pytest_mock import MockerFixture

from opcua_webhmi_bridge.frontend_messaging import FrontendMessagingWriter

LogRecordsType = Callable[[], List[logging.LogRecord]]


@pytest.fixture
def log_records(caplog: LogCaptureFixture) -> LogRecordsType:
    def _inner() -> List[logging.LogRecord]:
        return list(
            filter(
                lambda r: r.name == FrontendMessagingWriter.logger.name,
                caplog.records,
            )
        )

    return _inner


@pytest.fixture
def messaging_writer(
    httpserver: HTTPServer,
    mocker: MockerFixture,
) -> FrontendMessagingWriter:
    mocker.patch("opcua_webhmi_bridge.frontend_messaging.HEARTBEAT_TIMEOUT", 0.5)
    config = mocker.Mock(
        api_url=httpserver.url_for("/api"),
        **{"api_key.get_secret_value.return_value": "api_key"}
    )
    return FrontendMessagingWriter(config)


@pytest.fixture
def fake_message(mocker: MockerFixture) -> Any:
    return mocker.Mock(
        **{
            "message_type.value": "test_message",
            "frontend_data": {"payload": "test_payload"},
        }
    )


def test_initializes_superclass(messaging_writer: FrontendMessagingWriter) -> None:
    assert messaging_writer._queue.empty()


@dataclass
class RequestSuccesTestCase:
    expected_msg_type: str
    expected_payload: Union[str, None]
    timeout: bool


@dataclass
class RequestFailureTestCase:
    response_json: Dict[str, Any]
    response_status: int
    logged_error_contains: str


@pytest.mark.asyncio
class TestTask:
    @pytest.mark.parametrize(
        "testcase",
        [
            RequestSuccesTestCase("test_message", "test_payload", False),
            RequestSuccesTestCase("heartbeat", None, True),
        ],
        ids=[
            "OPC message",
            "Heartbeat message",
        ],
    )
    async def test_request_success(
        self,
        event_loop: asyncio.AbstractEventLoop,
        fake_message: Any,
        httpserver: HTTPServer,
        log_records: LogRecordsType,
        messaging_writer: FrontendMessagingWriter,
        testcase: RequestSuccesTestCase,
    ) -> None:
        httpserver.expect_oneshot_request(
            "/api",
            method="POST",
            headers={"Authorization": "apikey api_key"},
            json={
                "method": "publish",
                "params": {
                    "channel": testcase.expected_msg_type,
                    "data": {
                        "payload": testcase.expected_payload,
                    },
                },
            },
        ).respond_with_json({})
        task = event_loop.create_task(messaging_writer.task())
        if not testcase.timeout:
            messaging_writer.put(fake_message)
        await asyncio.sleep(0.6 if testcase.timeout else 0.1)
        assert len(httpserver.log) > 0
        httpserver.check_assertions()
        assert not any(r.levelno == logging.ERROR for r in log_records())
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.parametrize(
        "testcase",
        [
            RequestFailureTestCase({}, 404, "404"),
            RequestFailureTestCase(
                {"error": {"code": 102, "message": "namespace not found"}},
                200,
                "Centrifugo API error: 102 namespace not found",
            ),
        ],
        ids=[
            "Error 404",
            "Centrifugo API error",
        ],
    )
    async def test_request_failure(
        self,
        event_loop: asyncio.AbstractEventLoop,
        fake_message: Any,
        httpserver: HTTPServer,
        log_records: LogRecordsType,
        messaging_writer: FrontendMessagingWriter,
        testcase: RequestFailureTestCase,
    ) -> None:
        httpserver.expect_oneshot_request(
            "/api",
            headers={"Authorization": "apikey api_key"},
        ).respond_with_json(testcase.response_json, status=testcase.response_status)
        task = event_loop.create_task(messaging_writer.task())
        messaging_writer.put(fake_message)
        await asyncio.sleep(0.1)
        assert len(httpserver.log) > 0
        httpserver.check_assertions()
        last_log_record = log_records()[-1]
        assert last_log_record.levelno == logging.ERROR
        assert testcase.logged_error_contains in last_log_record.message
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
