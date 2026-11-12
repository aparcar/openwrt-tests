# ruff: noqa: F811 (tests take the imported probe fixtures as arguments)
"""The package manager (apk) and online tools that fetch from
downloads.openwrt.org: package database, installing packages, HTTPS
downloads and the owut upgrade tool."""

from probe_fixtures import online, slot, wan_slot  # noqa: F401 (fixtures)


def test_apk_procd_installed(ssh_command):
    """apk's package database is readable and lists procd, the init system.

    A failure means apk can't see the installed packages, so installing,
    removing or upgrading packages won't work.
    """
    assert "procd" in "\n".join(ssh_command.run_check("apk list"))


# Online tests: the DUT reaches the internet through its WAN port, with the
# lab's labgrid-probe as its uplink (`online`). Skipped on QEMU, in labs
# without a probe and on boards without a cabled WAN port.

# Crash markers of ucode scripts (owut)
UCODE_ERRORS = ("Reference error", "Type error", "Runtime error", "Syntax error")


def test_apk_add(online, ssh_command):
    """apk installs a package from downloads.openwrt.org over the WAN port.

    This is how users add software to their device; a failure means package
    feeds, signatures or the WAN connection don't work with this firmware.
    """
    try:
        ssh_command.run_check("apk update && apk add ucert", timeout=300)
        assert ssh_command.run("apk info -e ucert")[2] == 0, "ucert not installed"
    finally:
        ssh_command.run("apk del ucert")


def test_downloads_reachable(online, ssh_command):
    """uclient-fetch gets a release index over HTTPS (certificate checked).

    A failure means the device can't make verified HTTPS downloads, e.g.
    because of missing CA certificates or a broken TLS library.
    """
    out = ssh_command.run_check(
        "uclient-fetch -q -O - https://downloads.openwrt.org/.versions.json"
    )
    assert "stable_version" in "\n".join(out), out


def test_owut_check(online, ssh_command):
    """The online upgrade tool `owut check` runs against the live release
    metadata without crashing (e.g. on a branch whose `latest` is null), so
    users can check for and plan firmware upgrades.

    Regression: openwrt/openwrt#25976
    """
    ssh_command.run_check("apk update && apk add owut", timeout=300)
    for cmd in ("owut check", "owut check --version-to 25.12"):
        out, err, rc = ssh_command.run(cmd, timeout=180)
        text = "\n".join(out + err)
        crashes = [e for e in UCODE_ERRORS if e in text]
        assert not crashes and rc not in (254, 255), f"{cmd} crashed (rc {rc}):\n{text}"
