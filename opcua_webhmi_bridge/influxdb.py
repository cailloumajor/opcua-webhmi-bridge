from __future__ import annotations

import asyncio
import logging
from itertools import chain, starmap
from typing import Any, Dict, Iterator, List, Tuple, TypedDict, Union

import aioinflux
import tenacity

from .config import config
from .pubsub import OPCDataChangeMessage

queue: asyncio.Queue[OPCDataChangeMessage] = asyncio.Queue(maxsize=600)


class InfluxPoint(TypedDict):
    measurement: str
    tags: Dict[str, str]
    fields: Dict[str, Union[None, bool, float, int, str]]


def flatten(data: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a JSON data structure"""

    def unpack(parent_key: str, parent_value: Any) -> Iterator[Tuple[str, Any]]:
        """Unpack one level of nesting in JSON data structure"""
        if isinstance(parent_value, dict):
            for key, value in parent_value.items():
                yield f"{parent_key}.{key}", value
        elif isinstance(parent_value, list):
            for index, value in enumerate(parent_value):
                yield f"{parent_key}[{index}]", value
        else:
            yield parent_key, parent_value

    def remaining_work() -> bool:
        return any(isinstance(v, t) for v in data.values() for t in (dict, list))

    while remaining_work():
        data = dict(chain.from_iterable(starmap(unpack, data.items())))

    return data


def to_influx(message: OPCDataChangeMessage) -> Union[InfluxPoint, List[InfluxPoint]]:
    data: Union[List[Dict[str, Any]], Dict[str, Any]] = message.to_python()["data"]
    measurement = message.node_id.replace('"', "")
    if isinstance(data, list):
        index_tag = measurement.split(".")[-1] + "_index"
        return [
            {
                "measurement": measurement,
                "tags": {index_tag: str(index)},
                "fields": flatten(elem),
            }
            for index, elem in enumerate(data)
        ]
    else:
        return {"measurement": measurement, "tags": {}, "fields": flatten(data)}


async def task() -> None:
    logging.debug("InfluxDB writer task running")
    async with aioinflux.InfluxDBClient(
        host=config.influx_host, port=config.influx_port, db=config.influx_db_name,
    ) as client:
        while True:
            points = to_influx(await queue.get())
            retryer = tenacity.AsyncRetrying(  # type: ignore
                wait=tenacity.wait_fixed(5),
                before=tenacity.before_log(logging, logging.DEBUG),
                before_sleep=tenacity.before_sleep_log(logging, logging.INFO),
            )
            await retryer.call(client.write, points)
