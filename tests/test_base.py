from conftest import ubus_call
import re


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


def test_ssh(ssh_command):
    ssh_command.run("true")


def test_kernel_errors(shell_command):
    logread = "\n".join(shell_command.run("logread")[0])

    error_patterns = [
        r"traps:.*general protection",
        r"segfault at [[:digit:]]+ ip",
        r"error.*in",
        r"do_page_fault\(\): sending",
        r"Unable to handle kernel.*address",
        r"(PC is at |pc : )([^+\[ ]+).*",
        r"epc\s+:\s+\S+\s+([^+ ]+).*",
        r"EIP: \[<.*>\] ([^+ ]+).*",
    ]

    for pattern in error_patterns:
        assert re.search(pattern, logread) is None, f"Found kernel error: {pattern}"
