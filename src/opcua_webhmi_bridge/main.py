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
from .opcua import OPCUAClient

app = typer.Typer(add_completion=False)


class EnvVarsEpilogCommand(typer.core.TyperCommand):
    def format_epilog(
        self,
        ctx: click.Context,  # noqa: U100
        formatter: click.HelpFormatter,
    ) -> None:
        """Writes each line of the epilog, thus preserving newlines."""
        with formatter.section("Environment variables"):
            formatter.write_dl(Settings.help())


async def shutdown(
    loop: asyncio.AbstractEventLoop, sig: Optional[signal.Signals] = None
) -> None:
    """Cleanup tasks tied to the service's shutdown"""
    if sig:
        logging.info("Received exit signal %s", sig.name)
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    for task in tasks:
        task.cancel()

    logging.info("Waiting for %s outstanding tasks to finish...", len(tasks))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if not isinstance(result, asyncio.CancelledError) and isinstance(
            result, Exception
        ):
            logging.error("Exception occured during shutdown: %s", result)
    loop.stop()


def handle_exception(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
    # context["message"] will always be there; but context["exception"] may not
    try:
        exc: Exception = context["exception"]
    except KeyError:
        logging.error("Caught exception: %s", context["message"])
    else:
        logging.error("Caught exception %s: %s", exc.__class__.__name__, exc)
    logging.info("Shutting down...")
    asyncio.create_task(shutdown(loop))


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
        format="%(asctime)s %(levelname)s %(name)s:%(message)s",
        level=logging.DEBUG if verbose else logging.INFO,
    )
    if not verbose:
        for logger in [
            "asyncua.common.subscription",
            "asyncua.client.ua_client.UASocketProtocol",
        ]:
            logging.getLogger(logger).setLevel(logging.ERROR)

    try:
        env_settings = Settings(env_file)
    except ConfigError as err:
        logging.critical(err)
        logging.info("See `--help` option for more informations")
        sys.exit(2)

    if print_config:
        print(env_settings)
        sys.exit()

    loop = asyncio.get_event_loop()
    loop.set_debug(verbose)

    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    for s in signals:
        loop.add_signal_handler(
            s, lambda s=s: asyncio.create_task(shutdown(loop, sig=s))
        )
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

    try:
        loop.run_until_complete(centrifugo_proxy_server.start())
        loop.create_task(frontend_messaging_writer.run_task())
        loop.create_task(frontend_messaging_writer.heartbeat_task())
        loop.create_task(influx_writer.run_task())
        loop.create_task(opc_client.retrying_task())
        loop.run_forever()
    finally:
        loop.close()
        logging.info("Shutdown successfull")
