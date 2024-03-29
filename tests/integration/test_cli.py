import subprocess

import pytest

from .conftest import MainProcessFixture


def test_help_option(main_process: MainProcessFixture) -> None:
    process = main_process(["--help"])
    assert process.wait(timeout=5.0) == 0


def test_fails_without_args(main_process: MainProcessFixture) -> None:
    process = main_process([])
    assert process.wait(timeout=5.0) == 2


def test_runs_with_mandatory_args(
    main_process: MainProcessFixture,
    mandatory_env_args: dict[str, str],
) -> None:
    process = main_process([], mandatory_env_args)
    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=1.0)
