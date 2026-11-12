"""The basics: the firmware boots, gives a shell and SSH, logs no kernel
errors, reports the right version and board, and can make a configuration
backup. If these fail, little else on the device can work.

Runs before every other module (see pytest_collection_modifyitems in
conftest.py) and in this order: the first test boots the device, so its log
is the boot log, and a device without a shell or SSH fails here first."""

import os
import tarfile
import time

import pytest
from conftest import ubus_call
from kernel_log import kernel_errors


def test_shell(shell_command):
    """The firmware boots to a working shell on the serial console.

    If this fails, the image doesn't boot on this device at all (or the lab
    couldn't get it onto the device), and the run counts as a boot failure.
    """
    shell_command.run_check("true")


def test_dropbear_startup(shell_command):
    """The SSH server (dropbear) creates its host key and listens on port 22
    within two minutes of boot, so the device can be managed over the network.

    Runs before test_ssh: SSHDriver only retries for 30 s, and dropbear may
    take longer to generate its host keys on a first boot.
    """
    for _ in range(120):
        if (
            shell_command.run("ls /etc/dropbear/dropbear_rsa_host_key")[2] == 0
            and shell_command.run("netstat -tlpn | grep 0.0.0.0:22")[2] == 0
        ):
            return
        time.sleep(1)

    assert False, "Dropbear did not start up within 120 seconds"


def test_ssh(ssh_command):
    """The device accepts SSH logins over its LAN port.

    Without SSH a user can't manage the device remotely, and most of the
    other tests can't run.
    """
    ssh_command.run_check("true")


def test_kernel_errors(ssh_command):
    """The kernel log after boot has no critical errors (oopses, panics,
    driver or filesystem failures), which point to hardware support that is
    broken in this firmware.

    Patterns live in kernel_log.py, shared with test_sysupgrade. Runs right
    after boot, so it checks the boot and not what later tests did."""
    errors_found = kernel_errors(ssh_command.run_check("dmesg"))

    assert not errors_found, (
        f"Critical errors found in kernel log: {errors_found[:5]}"
    )  # Show first 5


def test_firmware_version(shell_command, record_property):
    """The device runs the firmware that was meant to be tested and records
    its build ID.

    If FIRMWARE_VERSION is set, the build ID must match it. A mismatch means
    the device booted an old or wrong image and the results describe a
    different firmware.
    """
    [actual_version] = shell_command.run_check("source /etc/os-release; echo $BUILD_ID")
    record_property("firmware_version", actual_version)

    if "FIRMWARE_VERSION" in os.environ:
        expected_version = os.environ["FIRMWARE_VERSION"]
        record_property("expected_firmware_version", expected_version)
        assert actual_version == expected_version, (
            f"Firmware version mismatch: expected {expected_version}, got {actual_version}"
        )


def test_echo(ssh_command):
    """Commands run over SSH return their output unchanged, a basic check
    that the shell and the SSH session work."""
    [output] = ssh_command.run_check("echo 'hello world'")
    assert output == "hello world"


def test_uname(ssh_command):
    """`uname -a` works and reports a Linux system, a basic sanity check of
    the userland."""
    [output] = ssh_command.run_check("uname -a")
    assert "GNU/Linux" in output


def test_ubus_system_board(ssh_command, record_property):
    """The system bus (ubus) answers and reports an OpenWrt release; records
    the board name, kernel, revision, rootfs type and target.

    ubus is how LuCI and most OpenWrt services talk to the system, so a
    failure here breaks the web interface and configuration tools.
    """
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
    """`sysupgrade -b` creates a configuration backup that contains the
    configuration files (e.g. /etc/config/dropbear).

    Users rely on these backups to restore their settings after a reset or
    to keep them across an upgrade.
    """
    try:
        ssh_command.run_check("sysupgrade -b /tmp/backup.tar.gz")
        ssh_command.get("/tmp/backup.tar.gz")

        with tarfile.open("backup.tar.gz", "r") as backup:
            assert "etc/config/dropbear" in backup.getnames()
    finally:
        ssh_command.run("rm -rf /tmp/backup.tar.gz")


@pytest.mark.lg_feature("rootfs")
def test_sysupgrade_backup_u(ssh_command):
    """`sysupgrade -u -b` makes a backup of only the files changed from the
    defaults, leaving out unchanged ones such as /etc/config/dropbear.

    -u leaves out files equal to their /rom copy; ext4 images (no squashfs)
    have no /rom, so there is nothing to compare against and it's skipped.
    """
    _, _, rc = ssh_command.run("test -d /rom/etc")
    if rc != 0:
        pytest.skip("no /rom (ext4 rootfs): sysupgrade -u keeps every file")
    try:
        ssh_command.run_check("sysupgrade -u -b /tmp/backup.tar.gz")
        ssh_command.get("/tmp/backup.tar.gz")

        with tarfile.open("backup.tar.gz", "r") as backup:
            assert "etc/config/dropbear" not in backup.getnames()
    finally:
        ssh_command.run("rm -rf /tmp/backup.tar.gz")
