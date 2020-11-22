import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any, Callable, List

import pytest
from _pytest.logging import LogCaptureFixture
from pytest_httpserver import HTTPServer
from pytest_mock import MockerFixture

from opcua_webhmi_bridge.influxdb import InfluxDBWriter, InfluxPoint, flatten, to_influx

LogRecordsType = Callable[[], List[logging.LogRecord]]


@pytest.fixture
def log_records(caplog: LogCaptureFixture) -> LogRecordsType:
    def _inner() -> List[logging.LogRecord]:
        return list(
            filter(
                lambda r: r.name == InfluxDBWriter.logger.name,
                caplog.records,
            )
        )

    return _inner


@pytest.fixture
def influxdb_writer(
    httpserver: HTTPServer,
    mocker: MockerFixture,
) -> InfluxDBWriter:
    config = mocker.Mock(
        db_name="test_db",
        host=httpserver.host,
        port=httpserver.port,
    )
    return InfluxDBWriter(config)


@pytest.fixture
def patch_flatten(mocker: MockerFixture) -> None:
    mocker.patch("opcua_webhmi_bridge.influxdb.flatten", lambda data: data)


@pytest.fixture
def patch_to_influx(mocker: MockerFixture) -> Callable[[List[InfluxPoint]], None]:
    def _inner(messages: List[InfluxPoint]) -> None:
        mocker.patch("opcua_webhmi_bridge.influxdb.to_influx", lambda _: messages)

    return _inner


def test_initializes_superclass(influxdb_writer: InfluxDBWriter) -> None:
    assert influxdb_writer._queue.empty()


class TestFlatten:
    @pytest.mark.parametrize("data", [None, 0, "string", []])
    def test_non_dict_data(self, data: Any) -> None:
        with pytest.raises(AttributeError):
            flatten(data)

    def test_empty_data(self) -> None:
        assert flatten({}) == {}

    def test_success(self) -> None:
        data = {
            "field1": None,
            "field2": {"field1": 1, "field2": [2, "elem2"]},
            "field3": [3, False, {"field1": "value1", "field2": True}],
        }
        assert flatten(data) == {
            "field1": None,
            "field2.field1": 1,
            "field2.field2[0]": 2,
            "field2.field2[1]": "elem2",
            "field3[0]": 3,
            "field3[1]": False,
            "field3[2].field1": "value1",
            "field3[2].field2": True,
        }


@pytest.mark.usefixtures("patch_flatten")
class TestToInflux:
    def test_to_influx_list_payload(self, mocker: MockerFixture) -> None:
        data = [{"field1": "value1"}, {"field1": "abcd", "field2": 42}]
        message = mocker.Mock(node_id='"list"."node"', payload=data)
        assert to_influx(message) == [
            {
                "measurement": "list.node",
                "tags": {"node_index": "0"},
                "fields": {"field1": "value1"},
            },
            {
                "measurement": "list.node",
                "tags": {"node_index": "1"},
                "fields": {"field1": "abcd", "field2": 42},
            },
        ]

    def test_to_influx_dict_payload(self, mocker: MockerFixture) -> None:
        data = {"field1": 1, "field2": "value2"}
        message = mocker.Mock(node_id='"dict"."node"', payload=data)
        assert to_influx(message) == [
            {
                "measurement": "dict.node",
                "tags": {},
                "fields": {"field1": 1, "field2": "value2"},
            }
        ]


@dataclass
class RequestSuccessTestCase:
    influx_points: List[InfluxPoint]
    expected_data: str


@pytest.mark.asyncio
class TestTask:
    @pytest.mark.parametrize(
        "testcase",
        [
            RequestSuccessTestCase(
                [
                    {
                        "measurement": "test_measurement1",
                        "tags": {},
                        "fields": {
                            "field1": "value1",
                            "field2": 2,
                            "field3": 3.0,
                            "field4": True,
                        },
                    }
                ],
                'test_measurement1 field1="value1",field2=2i,field3=3.0,field4=True ',
            ),
            RequestSuccessTestCase(
                [
                    {
                        "measurement": "test_measurement2",
                        "tags": {"index": "1"},
                        "fields": {"field1": "value1"},
                    },
                    {
                        "measurement": "test_measurement2",
                        "tags": {"index": "2"},
                        "fields": {"field1": "value2"},
                    },
                ],
                (
                    'test_measurement2,index=1 field1="value1" '
                    "\n"
                    'test_measurement2,index=2 field1="value2" '
                ),
            ),
        ],
        ids=[
            "Single Influx point with multiple field types",
            "Multiple Influx points with same measurement",
        ],
    )
    async def test_request_success(
        self,
        event_loop: asyncio.AbstractEventLoop,
        httpserver: HTTPServer,
        influxdb_writer: InfluxDBWriter,
        log_records: LogRecordsType,
        mocker: MockerFixture,
        patch_to_influx: Callable[[List[InfluxPoint]], None],
        testcase: RequestSuccessTestCase,
    ) -> None:
        patch_to_influx(testcase.influx_points)
        httpserver.expect_oneshot_request(
            "/write",
            method="POST",
            data=testcase.expected_data,
            query_string={"db": "test_db"},
        ).respond_with_data(status=204)
        task = event_loop.create_task(influxdb_writer.task())
        influxdb_writer.put(mocker.Mock())
        await asyncio.sleep(0.1)
        httpserver.check_assertions()
        assert not any(r.levelno == logging.ERROR for r in log_records())
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_request_failure(
        self,
        event_loop: asyncio.AbstractEventLoop,
        httpserver: HTTPServer,
        influxdb_writer: InfluxDBWriter,
        log_records: LogRecordsType,
        mocker: MockerFixture,
        patch_to_influx: Callable[[List[InfluxPoint]], None],
    ) -> None:
        patch_to_influx(
            [
                {
                    "measurement": "test_measurement",
                    "tags": {},
                    "fields": {"key": "value"},
                }
            ]
        )
        httpserver.expect_oneshot_request(
            "/write",
            method="POST",
            data='test_measurement key="value" ',
            query_string={"db": "test_db"},
        ).respond_with_json(
            {"error": "error JSON"},
            status=404,
            headers={"X-Influxdb-Error": "error header"},
        )
        task = event_loop.create_task(influxdb_writer.task())
        influxdb_writer.put(mocker.Mock())
        await asyncio.sleep(0.1)
        httpserver.check_assertions()
        last_log_record = log_records()[-1]
        assert last_log_record.levelno == logging.ERROR
        assert "error JSON" not in last_log_record.message
        assert "error header" in last_log_record.message
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
