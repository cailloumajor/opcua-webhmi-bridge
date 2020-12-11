"""Application entrypoint module."""

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import click
import typer

from .config import ConfigError, Settings
from .frontend_messaging import CentrifugoProxyServer, FrontendMessagingWriter
from .influxdb import InfluxDBWriter
from .library import AsyncTask
from .opcua import OPCUAClient

LOGGING_FILTERS = {
    "asyncua.common.subscription": {
        "levelno": logging.INFO,
        "funcName": "publish_callback",
    },
    "asyncua.client.ua_client.UASocketProtocol": {
        "levelno": logging.INFO,
        "funcName": "open_secure_channel",
    },
}


_logger = logging.getLogger(__name__)

app = typer.Typer(add_completion=False)


class EnvVarsEpilogCommand(typer.core.TyperCommand):
    """Custom command class."""

    def format_epilog(
        self,
        ctx: click.Context,  # noqa: U100
        formatter: click.HelpFormatter,
    ) -> None:
        """Writes each line of the epilog, thus preserving newlines."""
        with formatter.section("Environment variables"):
            formatter.write_dl(Settings.help())


async def shutdown(sig: Optional[signal.Signals] = None) -> None:
    """Cleanups tasks tied to the service's shutdown.

    Args:
        sig: Optional; The signal that triggered the shutdown.
    """
    if sig:
        _logger.info("Received exit signal %s", sig.name)
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    for task in tasks:
        task.cancel()

    _logger.info("Waiting for %s outstanding tasks to finish...", len(tasks))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if not isinstance(result, asyncio.CancelledError) and isinstance(
            result, Exception
        ):
            _logger.error("Exception occured during shutdown: %s", result)
    loop = asyncio.get_running_loop()
    await loop.shutdown_asyncgens()
    loop.stop()


def handle_exception(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
    """Exception handler for event loop."""
    # context["message"] will always be there;
    # but context["exception"] and context["future"] may not
    try:
        exc: Exception = context["exception"]
        future = context["future"]
        future_name = "unknown"
        try:
            # If future is a Task, get its name
            future_name = future.get_name()
        except AttributeError:
            pass
        _logger.error(
            "Caught exception `%s` in %s task: %s",
            exc.__class__.__name__,
            future_name,
            exc,
        )
    except KeyError:
        _logger.error("Caught exception: %s", context["message"])
    _logger.info("Shutting down...")
    loop.create_task(shutdown())


@app.command(cls=EnvVarsEpilogCommand)
def main(
    env_file: Optional[Path] = typer.Option(  # noqa: B008
        None, help="Path of a file containing configuration environment variables"
    ),
    print_config: bool = typer.Option(  # noqa: B008
        False, "--config", help="Print configuration object and exit"
    ),
    verbose: bool = typer.Option(  # noqa: B008
        False, "--verbose", "-v", help="Be more verbose (print debug informations)"
    ),
) -> None:
    """Bridge between OPC-UA server and web-based HMI."""
    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(name)s : %(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
    )

    def logging_filter(record: logging.LogRecord) -> bool:
        return not all(
            getattr(record, attr, None) == value
            for attr, value in LOGGING_FILTERS[record.name].items()
        )

    if not verbose:
        for logger in LOGGING_FILTERS.keys():
            logging.getLogger(logger).addFilter(logging_filter)

    try:
        env_settings = Settings(env_file)
    except ConfigError as err:
        _logger.critical("%s. See `--help` option for more informations", err)
        sys.exit(2)

    if print_config:
        print(env_settings)
        sys.exit()

    loop = asyncio.get_event_loop()
    loop.set_debug(verbose)

    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        loop.add_signal_handler(sig, lambda sig=sig: loop.create_task(shutdown(sig)))
    loop.set_exception_handler(handle_exception)

    frontend_messaging_writer = FrontendMessagingWriter(env_settings.centrifugo)
    centrifugo_proxy_server = CentrifugoProxyServer(
        env_settings.centrifugo, frontend_messaging_writer
    )
    influx_writer = InfluxDBWriter(env_settings.influx)
    opc_client = OPCUAClient(
        env_settings.opc,
        centrifugo_proxy_server,
        influx_writer,
        frontend_messaging_writer,
    )

    task: AsyncTask
    for task in [
        frontend_messaging_writer,
        centrifugo_proxy_server,
        influx_writer,
        opc_client,
    ]:
        task.run(loop)

    try:
        loop.run_forever()
    finally:
        loop.close()
        _logger.info("Shutdown successfull")
