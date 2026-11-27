"""Reboot, power off and debugfs on real hardware, with known upstream bugs.

`reboot` must bring the device back to its bootloader, `poweroff` must keep
it off, and reading debugfs must not crash the kernel. Where an open
openwrt/openwrt issue breaks this on a lab device, the test is marked xfail
for that device only (not strict), so the result shows XPASS once it is fixed.

These tests end the running system (reboot, poweroff, or an oops with
panic_on_oops). Afterwards they switch the strategy to "off": the next test's
`shell_command` powers the device on and boots the firmware again, as for the
first test. They only run with strategies that have an "off" state and real
power control (not QEMU, where a guest poweroff ends the QEMU process).
"""

import re
import time

import pexpect
import pytest
from kernel_log import kernel_errors
from labgrid.exceptions import NoDriverFoundError

# The first lines a bootloader or a fresh kernel prints after a reset
BOOT_BANNER = re.compile(
    rb"U-Boot (SPL )?\d{4}\.\d{2}"  # U-Boot version line
    rb"|BL2: v"  # MediaTek TF-A (OpenWrt One)
    rb"|stop autoboot"  # U-Boot, APBoot, Enterasys
    rb"|CFE version"  # Broadcom CFE
    rb"|Linux version \d"  # kernel start, whatever loaded it
)
REBOOT_TIMEOUT = 120
POWEROFF_WATCH = 40


def _openwrt(env, key):
    return (env.config.data.get("openwrt") or {}).get(key, "")


def _known_issue(request, reason):
    request.node.add_marker(pytest.mark.xfail(reason=reason))


@pytest.fixture
def power_strategy(strategy):
    """The strategy, if it can power the device off and boot it again."""
    states = type(strategy.status).__members__
    if "off" not in states or "qemu" in getattr(strategy, "bindings", {}):
        pytest.skip("needs a strategy with an off state on real hardware")
    return strategy


def _end_session(target, shell, command):
    """Send a command that ends the system on the raw serial console.

    SSH is deactivated first: its connection dies with the system, and the
    next ssh_command must connect to the new one."""
    try:
        target.deactivate(target.get_driver("SSHDriver", activate=False))
    except NoDriverFoundError:
        pass
    console = shell.console
    console.sendline(command)
    return console


# First in the module: it runs on the system the earlier modules booted
@pytest.mark.xfail(
    reason="openwrt/openwrt#17756: qcom_socinfo debugfs reads oops on ipq806x"
)
@pytest.mark.timeout(600)  # boot, reads
def test_qcom_socinfo_debugfs(env, target, power_strategy, shell_command):
    """Reading every file in /sys/kernel/debug/qcom_socinfo works without a
    kernel oops (ipq806x only).

    The files describe the SoC. A crash here means any tool or user that
    reads them reboots the device (OpenWrt panics on oops).

    The file names are echoed before each read, so the console log shows
    which file crashed.
    """
    if not _openwrt(env, "target").startswith("ipq806x"):
        pytest.skip("qcom_socinfo debugfs: ipq806x only")
    shell = shell_command
    _, _, unmounted = shell.run("grep -q ' /sys/kernel/debug debugfs ' /proc/mounts")
    if unmounted:
        shell.run_check("mount -t debugfs debugfs /sys/kernel/debug")
    alive = True
    try:
        _, _, rc = shell.run("test -d /sys/kernel/debug/qcom_socinfo")
        if rc:
            pytest.skip("no /sys/kernel/debug/qcom_socinfo (qcom_socinfo not loaded)")
        before = len(shell.run_check("dmesg"))
        alive = False
        shell.run(
            "find /sys/kernel/debug/qcom_socinfo -type f | while read f; do "
            'echo "$f"; cat "$f" > /dev/null; done',
            timeout=60,
        )
        new = shell.run_check("dmesg")[before:]
        alive = True
        errors = kernel_errors(new)
        assert not errors, f"kernel errors reading qcom_socinfo: {errors[:5]}"
    finally:
        if not alive:
            # the read crashed the kernel: boot again for the next test, and
            # drop the SSH connection (its keepalive died with the kernel)
            try:
                target.deactivate(target.get_driver("SSHDriver", activate=False))
            except NoDriverFoundError:
                pass
            power_strategy.transition("off")
        elif unmounted:
            shell.run("umount /sys/kernel/debug")


@pytest.mark.timeout(600)  # boot, reboot
def test_reboot(env, target, power_strategy, shell_command, request, record_property):
    """`reboot` restarts the device: the kernel reaches its restart handler
    and the bootloader (or a new kernel) starts within two minutes.

    A failure means the device hangs on reboot and stays dead until someone
    pulls the power, e.g. after an upgrade or a scheduled reboot.
    """
    release = shell_command.run_check(". /etc/openwrt_release; echo $DISTRIB_RELEASE")
    if _openwrt(env, "profile") == "ignitenet_ss-w2-ac2600" and "SNAPSHOT" in "".join(
        release
    ):
        # 6.18 snapshots, IPQ8064 like the reported Archer C2600
        _known_issue(request, "openwrt/openwrt#26753: ipq806x hangs on reboot")

    console = _end_session(target, shell_command, "reboot")
    try:
        start = time.monotonic()
        index, _, _, _ = console.expect(
            [r"Restarting system", pexpect.TIMEOUT], timeout=REBOOT_TIMEOUT
        )
        assert index == 0, "the kernel never reached its restart handler"
        index, _, match, _ = console.expect(
            [BOOT_BANNER, pexpect.TIMEOUT], timeout=REBOOT_TIMEOUT
        )
        record_property("reboot_seconds", round(time.monotonic() - start, 1))
        assert index == 0, "no bootloader or kernel start after 'Restarting system'"
        record_property("reboot_banner", match.group(0).decode(errors="replace"))
    finally:
        power_strategy.transition("off")


@pytest.mark.timeout(600)  # boot, power down, watch
def test_poweroff(target, power_strategy, shell_command, record_property):
    """`poweroff` reaches the kernel's power down, and the device stays off:
    no bootloader or kernel starts within 40 seconds after it.

    A failure means the kernel never powered down. A device that boots again
    on its own afterwards is an expected failure (xfail), not a failure:
    many embedded boards can't switch themselves off and restart instead
    (a watchdog that keeps running, or firmware that resets the SoC, e.g.
    openwrt/openwrt#18481 on the OpenWrt One, TF-A BL31 v2.14 on the
    BPi-R4), which is fine for a device meant to run all the time.
    """
    console = _end_session(target, shell_command, "poweroff")
    try:
        index, _, _, _ = console.expect(
            [r"Power down|System halted", pexpect.TIMEOUT], timeout=REBOOT_TIMEOUT
        )
        assert index == 0, "the kernel never reached power down"
        start = time.monotonic()
        index, _, match, _ = console.expect(
            [BOOT_BANNER, pexpect.TIMEOUT], timeout=POWEROFF_WATCH
        )
        if index == 0:
            seconds = round(time.monotonic() - start, 1)
            record_property("poweroff_restart_seconds", seconds)
            banner = match.group(0).decode(errors="replace")
            pytest.xfail(f"booted again {seconds} s after power down ({banner})")
    finally:
        power_strategy.transition("off")
