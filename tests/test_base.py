"""The basics, run before every other module (see pytest_collection_modifyitems
in conftest.py) and in this order: the first test boots the device, so its
log is the boot log, and a device without a shell or SSH fails here first."""

import os
import tarfile
import time

import pytest
from conftest import ubus_call
from kernel_log import kernel_errors


def test_shell(shell_command):
    shell_command.run_check("true")


# Before test_ssh: SSHDriver only retries for 30 s, and dropbear may take
# longer to generate its host keys on a first boot
def test_dropbear_startup(shell_command):
    for _ in range(120):
        if (
            shell_command.run("ls /etc/dropbear/dropbear_rsa_host_key")[2] == 0
            and shell_command.run("netstat -tlpn | grep 0.0.0.0:22")[2] == 0
        ):
            return
        time.sleep(1)

    assert False, "Dropbear did not start up within 120 seconds"


def test_ssh(ssh_command):
    ssh_command.run_check("true")


def test_kernel_errors(ssh_command):
    """Patterns live in kernel_log.py, shared with test_sysupgrade. Right
    after boot, so it checks the boot and not what later tests did."""
    errors_found = kernel_errors(ssh_command.run_check("dmesg"))

    assert not errors_found, (
        f"Critical errors found in kernel log: {errors_found[:5]}"
    )  # Show first 5


def test_firmware_version(shell_command, record_property):
    [actual_version] = shell_command.run_check("source /etc/os-release; echo $BUILD_ID")
    record_property("firmware_version", actual_version)

    if "FIRMWARE_VERSION" in os.environ:
        expected_version = os.environ["FIRMWARE_VERSION"]
        record_property("expected_firmware_version", expected_version)
        assert actual_version == expected_version, (
            f"Firmware version mismatch: expected {expected_version}, got {actual_version}"
        )


def test_echo(ssh_command):
    [output] = ssh_command.run_check("echo 'hello world'")
    assert output == "hello world"


def test_uname(ssh_command):
    [output] = ssh_command.run_check("uname -a")
    assert "GNU/Linux" in output


def test_ubus_system_board(ssh_command, record_property):
    output = ubus_call(ssh_command, "system", "board", {})
    assert output["release"]["distribution"] == "OpenWrt"

    record_property("board_name", output["board_name"])
    record_property("kernel", output["kernel"])
    record_property("revision", output["release"]["revision"])
    record_property("rootfs_type", output["rootfs_type"])
    record_property("target", output["release"]["target"])
    record_property("version", output["release"]["version"])


@pytest.mark.lg_feature("rootfs")
def test_sysupgrade_backup(ssh_command):
    try:
        ssh_command.run_check("sysupgrade -b /tmp/backup.tar.gz")
        ssh_command.get("/tmp/backup.tar.gz")

        with tarfile.open("backup.tar.gz", "r") as backup:
            assert "etc/config/dropbear" in backup.getnames()
    finally:
        ssh_command.run("rm -rf /tmp/backup.tar.gz")


@pytest.mark.lg_feature("rootfs")
def test_sysupgrade_backup_u(ssh_command):
    try:
        ssh_command.run_check("sysupgrade -u -b /tmp/backup.tar.gz")
        ssh_command.get("/tmp/backup.tar.gz")

        with tarfile.open("backup.tar.gz", "r") as backup:
            assert "etc/config/dropbear" not in backup.getnames()
    finally:
        ssh_command.run("rm -rf /tmp/backup.tar.gz")
