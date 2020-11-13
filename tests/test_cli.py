import subprocess

import pytest

COMMAND = "opcua-agent"


@pytest.mark.no_cover
def test_entrypoint() -> None:
    completed = subprocess.run(f"{COMMAND} --help", shell=True)
    assert completed.returncode == 0
