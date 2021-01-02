from pathlib import Path
from typing import List

import pytest
from _pytest.config import Config, PytestPluginManager
from _pytest.config.argparsing import Parser
from _pytest.main import Session
from _pytest.nodes import Item

INTEGRATION_MARKER = "integration"


def _sorting_key(item: Item) -> int:
    if item.get_closest_marker(INTEGRATION_MARKER):
        return 1
    return 0


def pytest_addoption(parser: Parser, pluginmanager: PytestPluginManager) -> None:
    del pluginmanager

    parser.addoption(
        "--only-integration", action="store_true", help="Only run integration tests"
    )
    parser.addoption(
        "--skip-integration", action="store_true", help="Skip integration tests"
    )


def pytest_configure(config: Config) -> None:
    config.addinivalue_line(
        "markers", f"{INTEGRATION_MARKER}: mark the test as an integration test"
    )
    # Disable pytest-cov if running only integration tests. See:
    # https://github.com/pytest-dev/pytest-cov/issues/418#issuecomment-657219659
    if config.option.only_integration:
        cov = config.pluginmanager.get_plugin("_cov")
        if cov:
            cov.options.no_cov = True
            if cov.cov_controller:
                cov.cov_controller.pause()


def pytest_collection_modifyitems(
    session: Session, config: Config, items: List[Item]
) -> None:
    del session

    for item in items:
        rel_path = Path(item.fspath).relative_to(config.rootpath / "tests")
        if rel_path.parts[0] == "integration":
            item.add_marker(INTEGRATION_MARKER)

    items.sort(key=_sorting_key)


def pytest_runtest_setup(item: Item) -> None:
    integration_marker = item.get_closest_marker(INTEGRATION_MARKER)
    only_integration: bool = item.config.option.only_integration
    skip_integration: bool = item.config.option.skip_integration
    if integration_marker is None:
        if only_integration:
            pytest.skip("Only run integration tests")
    else:
        if skip_integration:
            pytest.skip("Integration tests skipped")
