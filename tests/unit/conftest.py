import pytest
from pytest_httpserver import HeaderValueMatcher


@pytest.fixture(scope="session", autouse=True)
def authorization_matcher() -> None:
    del HeaderValueMatcher.DEFAULT_MATCHERS["Authorization"]
