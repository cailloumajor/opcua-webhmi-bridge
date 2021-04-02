"""Management of data writing to InfluxDB."""

from __future__ import annotations

import logging
from itertools import chain, starmap
from operator import itemgetter
from typing import Any, Dict, Iterator, List, NamedTuple, Tuple, Union

from aiohttp import ClientError, ClientSession, ClientTimeout
from yarl import URL

from .config import InfluxSettings
from .library import MessageConsumer
from .messages import OPCDataChangeMessage

# A JSON scalar can be null, but data here comes from OPC-UA,
# where a null value is not acceptable.
JsonScalar = Union[str, int, float, bool]
Flattened = Dict[str, JsonScalar]

_logger = logging.getLogger(__name__)


class UnexpextedScalarError(ValueError):
    """Unexpected scalar exception.

    Raised when a scalar in a node is found out of a structure.
    """

    def __init__(self, node_id: str):
        """Initializes unexpected scalar exception.

        Args:
            node_id: ID of the node containing unexpected scalar.
        """
        msg = f"`{node_id}` node: scalar found out of a structure"
        super().__init__(msg)


class InfluxDBWriteError(ClientError):
    """InfluxDB write error exception."""

    pass


class InfluxPoint(NamedTuple):
    """Represents an InfluxDB data point, excluding measurement.

    Attributes:
        tags: A dict of tags, keys and values all being strings.
        fields: A dict of flattened data.
    """

    tags: Dict[str, str]
    fields: Flattened


def flatten(data: Dict[str, Any]) -> Flattened:
    """Flattens a dictionary of data coming from JSON decoding.

    Args:
        data: The dictionary to flatten.

    Returns:
        A dictionary of flattened data.
    """

    def _unpack(parent_key: str, parent_value: Any) -> Iterator[Tuple[str, Any]]:
        if isinstance(parent_value, dict):
            for key, value in parent_value.items():
                yield f"{parent_key}.{key}", value
        elif isinstance(parent_value, list):
            for index, value in enumerate(parent_value):
                yield f"{parent_key}[{index}]", value
        else:
            yield parent_key, parent_value

    while any(isinstance(v, (dict, list)) for v in data.values()):
        data = dict(chain.from_iterable(starmap(_unpack, data.items())))

    return data


def to_influx(message: OPCDataChangeMessage) -> str:
    """Converts OPC-UA data change message to InfluxDB line protocol.

    Args:
        message: The OPC-UA data message.

    Returns:
        A string representing the data, in InfluxDB line protocol format.
    """

    def _influx_field_value(scalar: JsonScalar) -> str:
        """Converts a scalar to an InfluxDB field value representation."""
        # Treat boolean first, because it is also a instance of int
        if isinstance(scalar, bool):
            return str(scalar)
        elif isinstance(scalar, str):
            return f'"{scalar}"'
        elif isinstance(scalar, int):
            return f"{scalar}i"
        elif isinstance(scalar, float):
            return str(scalar)
        else:
            raise ValueError(f"Invalid InfluxDB field value: {scalar}")

    measurement = message.node_id.replace('"', "")
    points: List[InfluxPoint] = []
    lines: List[str] = []
    if isinstance(message.payload, list):
        index_tag = measurement.split(".")[-1] + "_index"
        for index, elem in enumerate(message.payload):
            if not isinstance(elem, dict):
                raise UnexpextedScalarError(message.node_id)
            points.append(InfluxPoint({index_tag: str(index)}, flatten(elem)))
    elif isinstance(message.payload, dict):
        points.append(InfluxPoint({}, flatten(message.payload)))
    else:
        raise UnexpextedScalarError(message.node_id)
    for point in points:
        line = measurement
        if point.tags:
            # InfluxDB documentation recommends to sort tags by key
            sorted_tags = sorted(point.tags.items(), key=itemgetter(0))
            line += "," + ",".join(f"{key}={value}" for key, value in sorted_tags)
        line += " "
        line += ",".join(
            f"{key}={_influx_field_value(value)}" for key, value in point.fields.items()
        )
        line += " "
        lines.append(line)

    return "\n".join(lines)


class InfluxDBWriter(MessageConsumer[OPCDataChangeMessage]):
    """Handles writing OPC-UA data to InfluxDB."""

    logger = _logger
    purpose = "InfluxDB writer"

    def __init__(self, config: InfluxSettings):
        """Initializes InfluxDB writer task.

        Args:
            config: InfluxDB related configuration options.
        """
        super().__init__()
        self._config = config

    async def task(self) -> None:
        """Implements InfluxDB writer asynchronous task."""
        headers = {"Authorization": f"Token {self._config.token}"}
        url = URL(self._config.base_url) / "api/v2/write"
        params = {
            "org": self._config.org,
            "bucket": self._config.bucket,
            "precision": "s",
        }
        async with ClientSession(
            headers=headers, timeout=ClientTimeout(total=10)
        ) as session:
            while True:
                line_protocol = to_influx(await self._queue.get())
                try:
                    async with session.post(
                        url, params=params, data=line_protocol
                    ) as resp:
                        if not resp.status == 204:
                            resp_data = await resp.json()
                            if (message := resp_data.get("message")) is not None:
                                raise InfluxDBWriteError(message)
                            resp.raise_for_status()
                except ClientError as err:
                    _logger.error("Write request error: %s", err)
