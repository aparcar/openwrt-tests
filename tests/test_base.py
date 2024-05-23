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
    print(logread)
    assert (
        re.search(
            r"(traps:.*general protection|segfault at [[:digit:]]+ ip.*error.*in)",
            logread,
        )
        is None
    )
    assert re.search(r"do_page_fault\(\): sending", logread) is None
    assert re.search(r"Unable to handle kernel.*address", logread) is None
    assert re.search(r"(PC is at |pc : )([^+\[ ]+).*", logread) is None
    assert re.search(r"epc\s+:\s+\S+\s+([^+ ]+).*", logread) is None
    assert re.search(r"EIP: \[<.*>\] ([^+ ]+).*", logread) is None
    assert (
        re.search(
            r"RIP: [[:xdigit:]]{4}:(\[<[[:xdigit:]]+>\] \[<[[:xdigit:]]+>\] )?([^+ ]+)\+0x.*",
            logread,
        )
        is None
    )
