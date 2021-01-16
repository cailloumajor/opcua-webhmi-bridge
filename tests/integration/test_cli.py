from .conftest import MainProcessFixture


def test_help_option(main_process: MainProcessFixture) -> None:
    process = main_process(["--help"])
    assert process.wait(timeout=5.0) == 0


def test_fails_without_args(main_process: MainProcessFixture) -> None:
    process = main_process([])
    assert process.wait(timeout=5.0) == 2
