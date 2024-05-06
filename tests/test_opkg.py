def test_opkg_procd_installed(ssh_command):
    assert "procd" in "\n".join(ssh_command.run("opkg list-installed")[0])


def test_opkg_install_tmate(ssh_command):
    ssh_command.run("opkg update")
    ssh_command.run("opkg install tmate")
    assert "tmate" in "\n".join(ssh_command.run("opkg list-installed")[0])
