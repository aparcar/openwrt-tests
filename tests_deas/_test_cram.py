from pathlib import Path

import prysk.test
import pytest


@pytest.fixture
def prysk_wrapper(shell_command):
    def _prysk_wrapper(command):
        data, _, returncode = shell_command.run((b"".join(command)).decode("utf-8"))
        return ("\n".join(data) + "x").encode(), returncode

    return _prysk_wrapper


@pytest.mark.parametrize(
    "cram_file",
    [Path("./tests/cram/base.t"), Path("./tests/cram/opkg.t")],
)
def test_cram(prysk_wrapper, cram_file):
    refout, postout, diff = prysk.test.testfile(cram_file, execute_func=prysk_wrapper)

    assert diff == []
