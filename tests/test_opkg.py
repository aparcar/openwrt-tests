import pytest


@pytest.mark.lg_feature("opkg")
def test_opkg_procd_installed(ssh_command):
    assert "procd" in "\n".join(ssh_command.run_check("opkg list-installed"))


@pytest.mark.lg_feature(["online", "opkg"])
def test_opkg_install_ucert(ssh_command):
    try:
        ssh_command.run_check("opkg update")
        ssh_command.run_check("opkg install ucert")
        assert "ucert" in "\n".join(ssh_command.run_check("opkg list-installed"))
    finally:
        ssh_command.run("opkg remove ucert")
