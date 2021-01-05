import asyncio
import contextlib
import logging
from typing import Any, Callable, Dict, List

import pytest
from pytest import LogCaptureFixture
from pytest_httpserver import HTTPServer
from pytest_mock import MockerFixture

from opcua_webhmi_bridge.influxdb import (
    InfluxDBWriter,
    UnexpextedScalarError,
    flatten,
    to_influx,
)

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
    config = mocker.Mock(db_name="test_db", root_url=httpserver.url_for("/influx"))
    return InfluxDBWriter(config)


@pytest.fixture
def patch_flatten(mocker: MockerFixture) -> None:
    mocker.patch("opcua_webhmi_bridge.influxdb.flatten", lambda data: data)


@pytest.fixture
def patch_to_influx(mocker: MockerFixture) -> None:
    mocker.patch(
        "opcua_webhmi_bridge.influxdb.to_influx",
        return_value="measurement,tag=tagval field=1.0 ",
    )


def test_initializes_superclass(influxdb_writer: InfluxDBWriter) -> None:
    assert influxdb_writer._queue.empty()


class TestFlatten:
    @pytest.mark.parametrize("data", [None, 0, "string", []])
    def test_non_dict_data(self, data: Any) -> None:
        with pytest.raises(AttributeError):
            flatten(data)

    def test_empty_data(self) -> None:
        assert flatten({}) == {}

    def test_flat_data(self) -> None:
        data = {
            "field1": 0.5,
            "field2": "value2",
            "field3": 42,
        }
        assert flatten(data) == data

    def test_not_flat_data(self) -> None:
        data = {
            "field1": 4.5,
            "field2": {"field1": 1, "field2": [2, "elem2"]},
            "field3": [3, False, {"field1": "value1", "field2": True}],
        }
        assert flatten(data) == {
            "field1": 4.5,
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
    @pytest.mark.parametrize("payload", ["string", 42, 5.4, True, None])
    def test_scalar_payload(self, mocker: MockerFixture, payload: Any) -> None:
        message = mocker.Mock(node_id="ScalarNode", payload=payload)
        with pytest.raises(UnexpextedScalarError, match=r"ScalarNode"):
            to_influx(message)

    def test_scalar_array_payload(self, mocker: MockerFixture) -> None:
        data = [1, 2, 3]
        message = mocker.Mock(node_id="ScalarArrayNode", payload=data)
        with pytest.raises(UnexpextedScalarError, match=r"ScalarArrayNode"):
            to_influx(message)

    def test_list_payload(self, mocker: MockerFixture) -> None:
        data = [{"field1": 5.6, "field2": True}, {"field1": "ab cd", "field2": 42}]
        message = mocker.Mock(node_id='"list"."node"', payload=data)
        assert to_influx(message) == "{0}\n{1}".format(
            "list.node,node_index=0 field1=5.6,field2=True ",
            'list.node,node_index=1 field1="ab cd",field2=42i ',
        )

    def test_dict_payload(self, mocker: MockerFixture) -> None:
        data = {"field1": 1, "field2": "value 2", "field3": 1.0, "field4": False}
        message = mocker.Mock(node_id='"dict"."node"', payload=data)
        expected = 'dict.node field1=1i,field2="value 2",field3=1.0,field4=False '
        assert to_influx(message) == expected

    def test_field_value_error(self, mocker: MockerFixture) -> None:
        data = {"field1": 1, "field2": None}
        message = mocker.Mock(node_id='"error"."node"', payload=data)
        with pytest.raises(ValueError, match="None"):
            to_influx(message)


@pytest.mark.asyncio
@pytest.mark.usefixtures("patch_to_influx")
class TestTask:
    async def test_request_success(
        self,
        event_loop: asyncio.AbstractEventLoop,
        httpserver: HTTPServer,
        influxdb_writer: InfluxDBWriter,
        log_records: LogRecordsType,
        mocker: MockerFixture,
    ) -> None:
        httpserver.expect_oneshot_request(
            "/influx/api/v2/write",
            method="POST",
            data="measurement,tag=tagval field=1.0 ",
            query_string={"bucket": "test_db", "precision": "s"},
        ).respond_with_json({}, status=204)
        task = event_loop.create_task(influxdb_writer.task())
        influxdb_writer.put(mocker.Mock())
        await asyncio.sleep(0.1)
        httpserver.check_assertions()
        assert not any(r.levelno == logging.ERROR for r in log_records())
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.parametrize(
        ["resp_json", "expected_message"],
        [
            ({"error": "error JSON"}, "error JSON"),
            ({"message": "error JSON"}, "error JSON"),
            ({}, "NOT FOUND"),
        ],
        ids=[
            "InfluxDB 1.8 error",
            "InfluxDB 2.0 error",
            "Empty response",
        ],
    )
    async def test_request_failure(
        self,
        event_loop: asyncio.AbstractEventLoop,
        expected_message: str,
        httpserver: HTTPServer,
        influxdb_writer: InfluxDBWriter,
        log_records: LogRecordsType,
        mocker: MockerFixture,
        resp_json: Dict[str, str],
    ) -> None:
        httpserver.expect_oneshot_request(
            "/influx/api/v2/write",
            method="POST",
            data="measurement,tag=tagval field=1.0 ",
            query_string={"bucket": "test_db", "precision": "s"},
        ).respond_with_json(resp_json, status=404)
        task = event_loop.create_task(influxdb_writer.task())
        influxdb_writer.put(mocker.Mock())
        await asyncio.sleep(0.1)
        httpserver.check_assertions()
        last_log_record = log_records()[-1]
        assert last_log_record.levelno == logging.ERROR
        assert expected_message in last_log_record.message
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
