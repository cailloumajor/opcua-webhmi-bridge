import asyncio
import contextlib
import logging
from typing import Callable, Iterator, TypedDict, Union

import pytest
from _pytest.logging import LogCaptureFixture
from _pytest.monkeypatch import MonkeyPatch
from pytest_httpserver import HTTPServer
from pytest_mock import MockerFixture

from opcua_webhmi_bridge.config import CentrifugoSettings
from opcua_webhmi_bridge.frontend_messaging import FrontendMessagingWriter
from opcua_webhmi_bridge.messages import LinkStatus, OPCStatusMessage

LogRecordsType = Callable[[], Iterator[logging.LogRecord]]


class ParamsData(TypedDict):
    payload: Union[str, None]


class Params(TypedDict):
    channel: str
    data: ParamsData


class CentrifugoCommand(TypedDict):
    method: str
    params: Params


def expected_data(message_type: str, payload: Union[str, None]) -> CentrifugoCommand:
    return {
        "method": "publish",
        "params": {
            "channel": message_type,
            "data": {
                "payload": payload,
            },
        },
    }


@pytest.fixture
def log_records(caplog: LogCaptureFixture) -> LogRecordsType:
    def inner() -> Iterator[logging.LogRecord]:
        return filter(
            lambda r: r.name == FrontendMessagingWriter.logger.name,
            caplog.records,
        )

    return inner


@pytest.fixture
def messaging_writer(
    monkeypatch: MonkeyPatch,
    httpserver: HTTPServer,
) -> FrontendMessagingWriter:
    monkeypatch.setenv("CENTRIFUGO_API_KEY", "api-key")
    monkeypatch.setenv("CENTRIFUGO_API_URL", httpserver.url_for("/api"))
    monkeypatch.setattr("opcua_webhmi_bridge.frontend_messaging.HEARTBEAT_TIMEOUT", 0.5)
    messaging_writer = FrontendMessagingWriter(CentrifugoSettings())
    return messaging_writer


def test_initializes_superclass(mocker: MockerFixture) -> None:
    instance = FrontendMessagingWriter(config=mocker.Mock())
    assert instance._queue.empty()


@pytest.mark.asyncio
async def test_http_requests(
    event_loop: asyncio.AbstractEventLoop,
    httpserver: HTTPServer,
    log_records: LogRecordsType,
    messaging_writer: FrontendMessagingWriter,
) -> None:

    expected_headers = {"Authorization": "apikey api_key"}
    message = OPCStatusMessage(LinkStatus.Up)

    # Step 1 expected request
    httpserver.expect_ordered_request(
        "/api",
        method="POST",
        json=expected_data("opc_status", "UP"),
        headers=expected_headers,
    ).respond_with_json({})
    # Step 2 expected request
    httpserver.expect_ordered_request(
        "/api",
        method="POST",
        json=expected_data("heartbeat", None),
        headers=expected_headers,
    ).respond_with_json({})
    # Step 3 expected request
    httpserver.expect_ordered_request(
        "/api", headers=expected_headers
    ).respond_with_data(status=418)
    # Step 4 expected request
    httpserver.expect_ordered_request(
        "/api", headers=expected_headers
    ).respond_with_json({"error": {"code": 102, "message": "namespace not found"}})

    task = event_loop.create_task(messaging_writer.task())
    # Step 1 - successfull request with status message
    messaging_writer.put(message)
    await asyncio.sleep(0.1)
    httpserver.check_assertions()
    # Step 2 - timeout waiting for message queue, heartbeat request
    await asyncio.sleep(0.5)
    httpserver.check_assertions()
    # Step 3 - failing request (error 418)
    messaging_writer.put(message)
    await asyncio.sleep(0.1)
    httpserver.check_assertions()
    last_log_record = list(log_records())[-1]
    assert last_log_record.levelno == logging.ERROR
    assert "HTTP error: 418 I'M A TEAPOT" in last_log_record.message
    # Step 4 - Centrifugo API error response
    messaging_writer.put(message)
    await asyncio.sleep(0.1)
    httpserver.check_assertions()
    last_log_record = list(log_records())[-1]
    assert last_log_record.levelno == logging.ERROR
    assert "Centrifugo API error: 102 namespace not found" in last_log_record.message

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
