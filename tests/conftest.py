from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, List

import pytest
from _pytest.config import Config
from _pytest.main import Session
from _pytest.nodes import Item

INTEGRATION_MARKER = "integration"

MANDATORY_ENV_ARGS = {
    "CENTRIFUGO_API_KEY": "key",
    "INFLUXDB_ORG": "test_org",
    "INFLUXDB_BUCKET": "test_bucket",
    "INFLUXDB_TOKEN": "test_token",
    "OPC_SERVER_URL": "opc.tcp://localhost:4840",
    "OPC_MONITOR_NODES": '["node1", "node2"]',
    "OPC_RECORD_NODES": '["node3", "node4"]',
}

if TYPE_CHECKING:

    class FixtureRequest:
        param: str


def _sorting_key(item: Item) -> int:
    if item.get_closest_marker(INTEGRATION_MARKER):
        return 1
    return 0


def pytest_configure(config: Config) -> None:
    config.addinivalue_line(
        "markers", f"{INTEGRATION_MARKER}: mark the test as an integration test"
    )


def pytest_collection_modifyitems(
    session: Session, config: Config, items: List[Item]
) -> None:
    del session

    for item in items:
        rel_path = Path(item.fspath).relative_to(config.rootpath / "tests")
        if rel_path.parts[0] == "integration":
            item.add_marker(INTEGRATION_MARKER)

    items.sort(key=_sorting_key)


@pytest.fixture
def mandatory_env_args() -> Dict[str, str]:
    return MANDATORY_ENV_ARGS


@pytest.fixture(params=MANDATORY_ENV_ARGS.keys())
def mandatory_env_args_keys(request: FixtureRequest) -> str:
    return request.param
