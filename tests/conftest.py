from pathlib import Path
from typing import List

from _pytest.config import Config
from _pytest.main import Session
from _pytest.nodes import Item

INTEGRATION_MARKER = "integration"


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
