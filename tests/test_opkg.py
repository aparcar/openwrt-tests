import pytest


@pytest.mark.lg_feature("opkg")
def test_opkg_procd_installed(ssh_command):
    assert "procd" in "\n".join(ssh_command.run("opkg list-installed")[0])


@pytest.mark.lg_feature(["online", "opkg"])
def test_opkg_install_ucert(ssh_command):
    ssh_command.run("opkg update")
    ssh_command.run("opkg install ucert")
    assert "ucert" in "\n".join(ssh_command.run("opkg list-installed")[0])
