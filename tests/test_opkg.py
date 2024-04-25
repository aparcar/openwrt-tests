def test_opkg_procd_installed(shell_command):
    assert "procd" in "\n".join(shell_command.run("opkg list-installed")[0])


def test_opkg_install_tmate(shell_command):
    shell_command.run("opkg update")
    shell_command.run("opkg install tmate")
    assert "tmate" in "\n".join(shell_command.run("opkg list-installed")[0])
