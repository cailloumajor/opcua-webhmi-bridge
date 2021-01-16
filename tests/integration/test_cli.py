from .conftest import MainProcessFixture


class TestEntrypoint:
    def test_help_option(self, main_process: MainProcessFixture) -> None:
        process = main_process(["--help"])
        assert process.wait(timeout=5.0) == 0

    def test_fails_without_args(self, main_process: MainProcessFixture) -> None:
        process = main_process([])
        assert process.wait(timeout=5.0) == 2
