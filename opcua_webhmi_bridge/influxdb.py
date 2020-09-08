from __future__ import annotations

import logging
import warnings
from itertools import chain, starmap
from typing import Any, Dict, Iterator, List, Tuple, TypedDict, Union

from aiohttp import ClientError

from ._utils import GenericWriter
from .config import InfluxSettings
from .opcua import OPCDataChangeMessage

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=UserWarning)
    from aioinflux import InfluxDBClient, InfluxDBError


class InfluxPoint(TypedDict):
    measurement: str
    tags: Dict[str, str]
    fields: Dict[str, Union[None, bool, float, int, str]]


def flatten(data: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a JSON data structure"""

    def unpack(
        parent_key: str, parent_value: Union[Dict[str, Any], List[Dict[str, Any]], Any]
    ) -> Iterator[Tuple[str, Any]]:
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
    data: Union[List[Dict[str, Any]], Dict[str, Any]] = message.asdict()["data"]
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


class InfluxDBWriter(GenericWriter[OPCDataChangeMessage, InfluxSettings]):
    purpose = "InfluxDB"

    async def _task(self) -> None:
        async with InfluxDBClient(
            host=self._config.host, port=self._config.port, db=self._config.db_name,
        ) as client:
            while True:
                points = to_influx(await self._queue.get())
                try:
                    await client.write(points)
                except (ClientError, InfluxDBError) as err:
                    logging.error("InfluxDB write error: %s", err)
