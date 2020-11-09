from __future__ import annotations

import logging
import warnings
from itertools import chain, starmap
from typing import Any, Dict, Iterator, List, Tuple, TypedDict, Union

from aiohttp import ClientError, ClientTimeout

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=UserWarning)
    from aioinflux import InfluxDBClient, InfluxDBError

from ._library import MessageConsumer
from .config import InfluxSettings
from .messages import OPCDataChangeMessage

_logger = logging.getLogger(__name__)


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
    measurement = message.node_id.replace('"', "")
    if isinstance(message.payload, list):
        index_tag = measurement.split(".")[-1] + "_index"
        return [
            {
                "measurement": measurement,
                "tags": {index_tag: str(index)},
                "fields": flatten(elem),
            }
            for index, elem in enumerate(message.payload)
        ]
    else:
        return {
            "measurement": measurement,
            "tags": {},
            "fields": flatten(message.payload),
        }


class InfluxDBWriter(MessageConsumer[OPCDataChangeMessage]):
    logger = _logger
    purpose = "InfluxDB writer"

    def __init__(self, config: InfluxSettings):
        super().__init__()
        self._config = config

    async def task(self) -> None:
        async with InfluxDBClient(
            host=self._config.host,
            port=self._config.port,
            db=self._config.db_name,
            timeout=ClientTimeout(total=5),
        ) as client:
            while True:
                points = to_influx(await self._queue.get())
                try:
                    await client.write(points)
                except (ClientError, InfluxDBError) as err:
                    _logger.error("InfluxDB write error: %s", err)
