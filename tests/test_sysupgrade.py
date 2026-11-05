"""Real sysupgrade: flash an image, boot it, check that the system still works.

These tests write the device's storage (flash, SD card or QEMU disk), so they
only run on targets that declare the ``sysupgrade`` feature. They work over
the serial console, because sysupgrade closes every SSH session and reboots.

The image to install:

- ``LG_SYSUPGRADE_IMAGE``: a local path or an http(s) URL to a sysupgrade
  image for the device (``*-sysupgrade.bin``/``.itb``, or ``*-combined.img.gz``
  on x86, ``*-combined-efi.img.gz`` on armsr).
- Without it, targets whose ``openwrt.image.type`` is ``combined`` or
  ``combined-efi`` (x86 and armsr QEMU) reinstall the firmware they booted
  (``--firmware``): a combined image is also the sysupgrade image. Other
  targets skip, since they boot an initramfs that sysupgrade doesn't accept.

Uncompressed ``.img`` files are gzipped before the upload: x86 and armsr
sysupgrade accept ``.img.gz``, and the raw image doesn't fit into /tmp on a
128 MB VM.

``LG_SYSUPGRADE_TIMEOUT`` (seconds, default 600) bounds the time from starting
sysupgrade to the login prompt of the new system.

Tests that run after this module (alphabetical order: test_system_health,
test_wan, test_wifi) run on the upgraded system.
"""

import gzip
import re
import shutil
import tempfile
import time
import urllib.request
from os import getenv
from pathlib import Path

import pytest
from kernel_log import kernel_errors

REMOTE_IMAGE = "/tmp/sysupgrade-image"
MARKER_FILE = "/etc/openwrtci-sysupgrade-marker"
MARKER_UCI = "system.@system[0].description"
MARKER_VALUE = "openwrtci-sysupgrade-marker"
LOGIN_PROMPT = "Please press Enter to activate this console."


def _timeout():
    return float(getenv("LG_SYSUPGRADE_TIMEOUT", "600"))


@pytest.fixture(scope="module")
def sysupgrade_image(env, pytestconfig):
    """Local path of the image to install, gzipped where that's needed."""
    source = getenv("LG_SYSUPGRADE_IMAGE")
    image_type = (env.config.data.get("openwrt") or {}).get("image", {}).get("type")
    if not source:
        if image_type not in ("combined", "combined-efi"):
            pytest.skip(
                "set LG_SYSUPGRADE_IMAGE: this target boots an image "
                "sysupgrade does not accept"
            )
        source = pytestconfig.getoption("firmware")

    tmpdir = Path(tempfile.mkdtemp(prefix="sysupgrade-"))
    try:
        if re.match(r"https?://", source):
            local = tmpdir / Path(source.split("?")[0]).name
            with (
                urllib.request.urlopen(source, timeout=120) as res,
                open(local, "wb") as f,
            ):
                shutil.copyfileobj(res, f)
        else:
            local = Path(source)
            if not local.is_file():
                pytest.fail(f"sysupgrade image not found: {local}")

        if local.suffix == ".img":
            packed = tmpdir / (local.name + ".gz")
            with open(local, "rb") as src, gzip.open(packed, "wb") as dst:
                shutil.copyfileobj(src, dst)
            local = packed

        yield local
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _boot_id(ssh):
    return ssh.run_check("cat /proc/sys/kernel/random/boot_id")[0].strip()


def _revision(ssh):
    out = ssh.run_check(". /etc/openwrt_release; echo $DISTRIB_REVISION")
    return out[0].strip() if out else ""


def _upload(ssh, image):
    """Copy the image to /tmp, skipping if it doesn't fit into RAM."""
    free_kb = int(ssh.run_check("df -k /tmp | awk 'NR==2 {print $4}'")[0])
    size_kb = image.stat().st_size // 1024
    # sysupgrade needs RAM beyond the image itself (it copies tools to a ramfs)
    if size_kb + 16 * 1024 > free_kb:
        pytest.skip(f"image ({size_kb} KiB) does not fit into /tmp ({free_kb} KiB)")
    ssh.put(str(image), REMOTE_IMAGE)


def _console_reboot(target, strategy, ssh, command, record_property, name):
    """Run a command that reboots the DUT, wait for the new system, log in.

    The shell and SSH drivers are deactivated first: the command kills every
    session, and the ShellDriver has to await the login prompt again.

    For a sysupgrade, a reboot alone proves nothing: when the upgrade aborts
    (e.g. stage2 killed by the OOM killer) the device reboots into the old,
    unchanged system. So the upgrade must report "Upgrade completed" on the
    console; "sysupgrade aborted" fails the test (after logging in again, so
    later tests find a usable DUT).
    """
    shell = strategy.shell
    console = shell.console

    target.deactivate(ssh)
    target.deactivate(shell)

    start = time.monotonic()
    console.sendline(command)
    aborted = None
    if command.startswith("sysupgrade"):
        index, _, match, _ = console.expect(
            ["Upgrade completed", r"sysupgrade aborted[^\r\n]*"], timeout=_timeout()
        )
        if index == 1:
            aborted = match.group(0)
            if isinstance(aborted, bytes):
                aborted = aborted.decode(errors="replace")
    console.expect(LOGIN_PROMPT, timeout=_timeout())
    record_property(f"{name}_seconds", round(time.monotonic() - start, 1))

    target.activate(shell)
    # Wait for the LAN so the SSH driver can connect
    shell.run("ubus -t 120 wait_for network.interface.lan", timeout=130)
    shell.run(
        "for i in $(seq 60); do "
        "ifstatus lan | grep -q '\"up\": true' && break; sleep 2; done",
        timeout=130,
    )
    target.activate(ssh)
    if aborted:
        pytest.fail(f"{command}: {aborted} (the old system booted again)")


def _assert_healthy(ssh, record_property):
    """The checks that say the upgraded system works."""
    board = ssh.run_check("ubus call system board")
    assert any('"release"' in line for line in board), "ubus system board failed"

    revision = _revision(ssh)
    record_property("revision_after", revision)

    # Booted from storage with a writable overlay, not from RAM
    mounts = "\n".join(ssh.run_check("cat /proc/mounts"))
    overlay = re.search(r"^\S+ /overlay (\S+)", mounts, re.MULTILINE)
    assert overlay, "no /overlay mount"
    assert overlay.group(1) != "tmpfs", "overlay is tmpfs: not running from storage"
    ssh.run_check("touch /etc/.rw-check && rm /etc/.rw-check")

    lan = "\n".join(ssh.run_check("ifstatus lan"))
    assert '"up": true' in lan, "lan is not up"

    services = "\n".join(ssh.run_check("ubus call service list"))
    for service in ("dropbear", "rpcd", "network", "log", "dnsmasq"):
        assert f'"{service}"' in services, f"service {service} not running"

    tables = "\n".join(ssh.run_check("nft list tables"))
    assert "inet fw4" in tables, "firewall (fw4) not loaded"

    errors = kernel_errors(ssh.run_check("dmesg"))
    assert not errors, f"kernel errors after upgrade: {errors[:5]}"


def _set_markers(ssh):
    ssh.run_check(f"echo {MARKER_VALUE} > {MARKER_FILE}")
    ssh.run_check(
        f"grep -qx {MARKER_FILE} /etc/sysupgrade.conf || "
        f"echo {MARKER_FILE} >> /etc/sysupgrade.conf"
    )
    ssh.run_check(f"uci set {MARKER_UCI}={MARKER_VALUE} && uci commit system")


def _markers(ssh):
    """(file kept, uci kept)"""
    _, _, rc = ssh.run(f"grep -qx {MARKER_VALUE} {MARKER_FILE}")
    uci, _, _ = ssh.run(f"uci -q get {MARKER_UCI}")
    return rc == 0, bool(uci) and uci[0].strip() == MARKER_VALUE


@pytest.mark.lg_feature("sysupgrade")
def test_sysupgrade_rejects_invalid_image(env, ssh_command):
    """sysupgrade -T must refuse a file that is not an image."""
    try:
        ssh_command.run_check(f"head -c 65536 /dev/urandom > {REMOTE_IMAGE}")
        _, _, rc = ssh_command.run(f"sysupgrade -T {REMOTE_IMAGE}")
    finally:
        ssh_command.run(f"rm -f {REMOTE_IMAGE}")
    target = (env.config.data.get("openwrt") or {}).get("target", "")
    if rc == 0 and target.startswith("armsr"):
        # armsr's platform_check_image (target/linux/armsr/base-files/lib/
        # upgrade/platform.sh) only compares partition tables: when they
        # differ it logs "Full image will be written" and accepts the file
        pytest.xfail("armsr's platform_check_image accepts any file (upstream)")
    assert rc != 0, "sysupgrade accepted random data as an image"


@pytest.mark.lg_feature("sysupgrade")
def test_sysupgrade_image_check(ssh_command, sysupgrade_image):
    """sysupgrade -T accepts the image (metadata, board name, signature)."""
    try:
        _upload(ssh_command, sysupgrade_image)
        out, err, rc = ssh_command.run(f"sysupgrade -T {REMOTE_IMAGE}")
        assert rc == 0, f"image check failed: {out + err}"
    finally:
        ssh_command.run(f"rm -f {REMOTE_IMAGE}")


@pytest.mark.lg_feature("sysupgrade")
def test_sysupgrade_keep_settings(
    target, strategy, ssh_command, sysupgrade_image, record_property
):
    """Upgrade keeping settings: the new system boots, keeps the config
    (a uci option and a file listed in /etc/sysupgrade.conf) and works."""
    ssh = ssh_command
    record_property("revision_before", _revision(ssh))
    record_property("image_bytes", sysupgrade_image.stat().st_size)
    boot_id = _boot_id(ssh)

    _set_markers(ssh)
    _upload(ssh, sysupgrade_image)
    _console_reboot(
        target,
        strategy,
        ssh,
        f"sysupgrade {REMOTE_IMAGE}",
        record_property,
        "sysupgrade",
    )

    assert _boot_id(ssh) != boot_id, "the device did not reboot"
    kept_file, kept_uci = _markers(ssh)
    assert kept_uci, "uci setting lost by sysupgrade"
    assert kept_file, f"{MARKER_FILE} (in sysupgrade.conf) lost by sysupgrade"
    _assert_healthy(ssh, record_property)


@pytest.mark.lg_feature("sysupgrade")
def test_sysupgrade_persists_across_reboot(
    target, strategy, ssh_command, record_property
):
    """The installed system keeps changes across a plain reboot."""
    ssh = ssh_command
    boot_id = _boot_id(ssh)
    ssh.run_check("echo persist > /etc/openwrtci-reboot-marker && sync")

    _console_reboot(target, strategy, ssh, "reboot", record_property, "reboot")

    try:
        assert _boot_id(ssh) != boot_id, "the device did not reboot"
        out, _, rc = ssh.run("cat /etc/openwrtci-reboot-marker")
        assert rc == 0 and out == ["persist"], "file lost across reboot"
        _assert_healthy(ssh, record_property)
    finally:
        ssh.run("rm -f /etc/openwrtci-reboot-marker")


@pytest.mark.lg_feature("sysupgrade")
def test_sysupgrade_no_keep(
    target, strategy, ssh_command, sysupgrade_image, record_property
):
    """Upgrade with -n: the new system boots with default settings."""
    ssh = ssh_command
    boot_id = _boot_id(ssh)

    _set_markers(ssh)
    _upload(ssh, sysupgrade_image)
    _console_reboot(
        target,
        strategy,
        ssh,
        f"sysupgrade -n {REMOTE_IMAGE}",
        record_property,
        "sysupgrade",
    )

    assert _boot_id(ssh) != boot_id, "the device did not reboot"
    kept_file, kept_uci = _markers(ssh)
    assert not kept_uci, "sysupgrade -n kept a uci setting"
    assert not kept_file, "sysupgrade -n kept a file from sysupgrade.conf"
    _assert_healthy(ssh, record_property)
