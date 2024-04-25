from conftest import ubus_call


def test_shell(shell_command):
    shell_command.run("true")


def test_echo(shell_command):
    output = shell_command.run("echo 'hello world'")
    assert output[0][0] == "hello world"


def test_uname(shell_command):
    assert "GNU/Linux" in shell_command.run("uname -a")[0][0]


def test_ubus_system_board(shell_command):
    output = ubus_call(shell_command, "system", "board", {})
    assert output["release"]["distribution"] == "OpenWrt"
