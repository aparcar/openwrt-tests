import pytest


@pytest.mark.lg_feature("apk")
def test_apk_procd_installed(shell_command):
    assert "procd" in "\n".join(shell_command.run("apk list")[0])


@pytest.mark.lg_feature(["online", "apk"])
def test_apk_add_ucert(ssh_command):
    try:
        ssh_command.run_check("apk add ucert")
        assert "ucert" in "\n".join(ssh_command.run_check("apk list | grep ucert"))
    finally:
        ssh_command.run("apk del ucert")
