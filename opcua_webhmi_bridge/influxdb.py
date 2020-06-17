from __future__ import annotations

import asyncio
import logging
import warnings
from typing import Any, Dict

import tenacity

from .config import config

# Suppress aioinflux warnings about pandas and NumPy
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import aioinflux


measurement_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=1)


async def task() -> None:
    logging.debug("InfluxDB writer task running")
    async with aioinflux.InfluxDBClient(
        host=config.influx_host, port=config.influx_port, db=config.influx_db_name,
    ) as client:
        while True:
            point = {
                "measurement": config.influx_measurement,
                "fields": await measurement_queue.get(),
            }
            retryer = tenacity.AsyncRetrying(  # type: ignore
                wait=tenacity.wait_fixed(5),
                before=tenacity.before_log(logging, logging.DEBUG),
                before_sleep=tenacity.before_sleep_log(logging, logging.INFO),
            )
            await retryer.call(client.write, point)
