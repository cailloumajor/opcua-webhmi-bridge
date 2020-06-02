import asyncio
import logging
import warnings
from typing import NamedTuple

import tenacity

from .config import config

# Suppress aioinflux warnings about pandas and NumPy
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import aioinflux


@aioinflux.lineprotocol
class Production(NamedTuple):
    total_line: aioinflux.INT


measurement_queue = asyncio.Queue(maxsize=1)  # type: asyncio.Queue[Production]


async def task() -> None:
    logging.debug("InfluxDB writer task running")
    async with aioinflux.InfluxDBClient(
        host=config.influx_host, port=config.influx_port, db=config.influx_db_name,
    ) as client:
        while True:
            point = await measurement_queue.get()
            retryer = tenacity.AsyncRetrying(  # type: ignore
                wait=tenacity.wait_fixed(5),
                before=tenacity.before_log(logging, logging.DEBUG),
                before_sleep=tenacity.before_sleep_log(logging, logging.INFO),
            )
            await retryer.call(client.write, point)
