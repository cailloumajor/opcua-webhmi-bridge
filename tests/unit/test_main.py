import asyncio
import logging
import re
from pathlib import Path
from signal import SIGINT
from typing import Callable, List
from unittest.mock import AsyncMock as AsyncMockType

import pytest
from pytest import LogCaptureFixture
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from opcua_webhmi_bridge.config import ConfigError
from opcua_webhmi_bridge.main import _logger, app, handle_exception, shutdown


class ExceptionForTestingError(Exception):
    pass


async def dummy_task() -> None:
    while True:
        await asyncio.sleep(0.1)


async def raising_task() -> None:
    try:
        while True:
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        raise ExceptionForTestingError("Exception for testing")


LogRecordsType = Callable[[], List[logging.LogRecord]]


@pytest.fixture
def log_records(caplog: LogCaptureFixture) -> LogRecordsType:
    caplog.set_level(logging.INFO)

    def _inner() -> List[logging.LogRecord]:
        return list(filter(lambda r: r.name == _logger.name, caplog.records))

    return _inner


@pytest.fixture
def dummy_tasks(event_loop: asyncio.AbstractEventLoop) -> None:
    for index in range(5):
        event_loop.create_task(dummy_task(), name=f"dummy{index}")


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_shutdown(mocker: MockerFixture) -> AsyncMockType:
    return mocker.patch("opcua_webhmi_bridge.main.shutdown")


class TestShutdown:
    @pytest.mark.usefixtures("dummy_tasks")
    def test_cleanup_no_exception(self, event_loop: asyncio.AbstractEventLoop) -> None:
        assert len(asyncio.all_tasks(event_loop)) == 5
        event_loop.run_until_complete(shutdown())
        assert not event_loop.is_running()

    @pytest.mark.usefixtures("dummy_tasks")
    def test_cleanup_with_exception(
        self,
        event_loop: asyncio.AbstractEventLoop,
        log_records: LogRecordsType,
    ) -> None:
        event_loop.create_task(raising_task())
        assert len(asyncio.all_tasks(event_loop)) == 6
        event_loop.run_until_complete(shutdown())
        assert not event_loop.is_running()
        last_log_record = log_records()[-1]
        assert last_log_record.levelno == logging.ERROR
        assert "Exception for testing" in last_log_record.message

    def test_signal_logged(
        self,
        event_loop: asyncio.AbstractEventLoop,
        log_records: LogRecordsType,
    ) -> None:
        event_loop.run_until_complete(shutdown(SIGINT))
        expected_record = [
            rec for rec in log_records() if "Received exit signal SIGINT" in rec.message
        ]
        assert len(expected_record)


class TestExceptionHandler:
    def test_with_exc_and_task(
        self,
        event_loop: asyncio.AbstractEventLoop,
        log_records: LogRecordsType,
        mocker: MockerFixture,
        patched_shutdown: AsyncMockType,
    ) -> None:
        context = {
            "exception": ExceptionForTestingError("Exception handler test"),
            "future": mocker.Mock(**{"get_name.return_value": "test_task"}),
        }
        handle_exception(event_loop, context)
        event_loop.stop()
        event_loop.run_forever()
        expected_record = [
            rec
            for rec in log_records()
            if rec.levelno == logging.ERROR
            and "ExceptionForTesting" in rec.message
            and "test_task" in rec.message
            and "Exception handler test" in rec.message
        ]
        assert len(expected_record)
        assert patched_shutdown.called

    def test_with_exc_and_future(
        self,
        event_loop: asyncio.AbstractEventLoop,
        log_records: LogRecordsType,
        patched_shutdown: AsyncMockType,
    ) -> None:
        context = {
            "exception": ExceptionForTestingError("Exception handler test"),
            "future": asyncio.Future(),
        }
        handle_exception(event_loop, context)
        event_loop.stop()
        event_loop.run_forever()
        expected_record = [
            rec
            for rec in log_records()
            if rec.levelno == logging.ERROR
            and "ExceptionForTesting" in rec.message
            and "unknown" in rec.message
            and "Exception handler test" in rec.message
        ]
        assert len(expected_record)
        assert patched_shutdown.called

    def test_with_message(
        self,
        event_loop: asyncio.AbstractEventLoop,
        log_records: LogRecordsType,
        patched_shutdown: AsyncMockType,
    ) -> None:
        context = {"message": "exception message"}
        handle_exception(event_loop, context)
        event_loop.stop()
        event_loop.run_forever()
        expected_record = [
            rec
            for rec in log_records()
            if rec.levelno == logging.ERROR and "exception message" in rec.message
        ]
        assert len(expected_record)
        assert patched_shutdown.called


class TestApp:
    def test_help(
        self,
        cli_runner: CliRunner,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "opcua_webhmi_bridge.main.Settings.help",
            return_value=[
                ("ENV_VAR_1", "Help text 1"),
                ("ENV_VAR_2", "Help text 2"),
            ],
        )
        result = cli_runner.invoke(app, "--help")
        env_var_help_lines = re.findall(r"ENV_VAR_\d[ \t]+Help text \d", result.output)
        assert len(env_var_help_lines) == 2

    def test_env_config_error(
        self,
        cli_runner: CliRunner,
        log_records: LogRecordsType,
        mocker: MockerFixture,
    ) -> None:
        patched_settings = mocker.patch(
            "opcua_webhmi_bridge.main.Settings",
            side_effect=ConfigError(None, "config error"),
        )
        result = cli_runner.invoke(app, "--env-file=/path/to/env/file")
        assert patched_settings.call_args_list == [
            mocker.call(Path("/path/to/env/file"))
        ]
        expected_record = [
            rec
            for rec in log_records()
            if rec.levelno == logging.CRITICAL and "config error" in rec.message
        ]
        assert len(expected_record)
        assert result.exit_code == 2

    def test_print_config(self, cli_runner: CliRunner, mocker: MockerFixture) -> None:
        patched_settings = mocker.patch("opcua_webhmi_bridge.main.Settings")
        patched_settings.return_value.__str__.return_value = "settings instance"
        result = cli_runner.invoke(app, "--config")
        assert "settings instance" in result.output
        assert result.exit_code == 0
