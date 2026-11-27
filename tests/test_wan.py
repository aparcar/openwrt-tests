"""The DUT's WAN side: firewall zone, WAN interface and downloads from the
internet."""

# ruff: noqa: F811 (tests take the imported `online` fixture as an argument)
import pytest
from conftest import ubus_call
from probe_fixtures import online, slot, wan_slot  # noqa: F401 (fixtures)


def check_download(
    command,
    url,
    expect_stdout=None,
    expect_stderr=None,
    expect_exitcode=0,
    expect_content=None,
    remove=True,
    filename="index.html",
):
    try:
        stdout, stderr, exitcode = command.run(f"wget {url} -O {filename}")
        if expect_stdout:
            found = False
            for line in stdout:
                if expect_stdout in line:
                    found = True
                    break
            assert found, f"Expected output '{expect_stdout}' not found in {stdout}"

        if expect_stderr:
            found = False
            for line in stderr:
                if expect_stderr in line:
                    found = True
                    break
            assert found, f"Expected error '{expect_stderr}' not found in {stderr}"

        assert expect_exitcode == exitcode, (
            f"Expected exit code {expect_exitcode} not found in {exitcode}"
        )
        if expect_content:
            assert expect_content in command.run(f"cat {url.split('/')[-1]}")[0], (
                f"Expected content '{expect_content}' not found in {url.split('/')[-1]}"
            )
    finally:
        if remove:
            command.run(f"rm {filename}")


@pytest.mark.lg_feature("wan_port")
def test_wan_firewall_zone(shell_command):
    """The default firewall configuration has a zone named "wan". Without it
    the WAN port is not protected, and LAN-to-WAN masquerading is missing."""
    stdout, _, _ = shell_command.run(
        'uci show firewall | grep "=zone" | while read zone; do '
        "section=${zone%%=zone}; "
        'name=$(uci get "${section}.name"); '
        '[ "$name" = "wan" ] && echo found && exit 0; '
        "done"
    )
    assert "found" in stdout, "Firewall zone 'wan' not found"


@pytest.mark.lg_feature("wan_port")
def test_wan_interface_device(shell_command):
    """The "wan" network interface is bound to a network device. If the
    board's default config has no WAN port mapped, the device can't get an
    internet connection."""
    status = ubus_call(shell_command, "network.interface.wan", "status")
    device = status.get("device") or status.get("l3_device")
    assert device, "WAN interface has no device assigned"


@pytest.mark.lg_feature("online")
def test_https_download(ssh_command):
    """wget downloads a file from downloads.openwrt.org over HTTPS, and its
    checksum matches. This covers DNS, the uplink, TLS and the CA
    certificates, which package installs and updates depend on."""
    check_download(
        ssh_command,
        "https://downloads.openwrt.org/releases/21.02.2/targets/armvirt/64/config.buildinfo",
        expect_stderr="Download completed",
        filename="config.buildinfo",
        remove=False,
    )

    assert (
        "26b85383a138594b1197e581bd13c6825c0b6b5f23829870a6dbc5d37ccf6cd8  config.buildinfo"
        in ssh_command.run("sha256sum config.buildinfo")[0]
    )
    ssh_command.run("rm config.buildinfo")


# TLS certificate validation, against badssl.com. The DUT must accept a
# valid certificate and reject invalid ones; a device that fetches an
# invalid one has broken TLS verification (no CA bundle, a library that
# skips the check), so its HTTPS downloads and package installs are
# unauthenticated. `online` brings the DUT's uplink up through the probe.

# badssl.com hosts: the valid-certificate control, then certificates that
# must be rejected (untrusted CA, hostname mismatch, expired)
BADSSL_VALID = "https://sha256.badssl.com/"
BADSSL_INVALID = {
    "untrusted_root": "https://untrusted-root.badssl.com/",
    "wrong_host": "https://wrong.host.badssl.com/",
    "expired": "https://expired.badssl.com/",
}


def _fetch(ssh_command, url):
    """uclient-fetch `url` to /dev/null; its exit code (0 = fetched)."""
    return ssh_command.run(f"uclient-fetch -T 20 -q -O /dev/null {url}", timeout=60)[2]


def test_tls_valid_certificate(online, ssh_command):
    """The DUT fetches a page served with a valid certificate. This is the
    control for the rejection tests: if it fails, those prove nothing."""
    assert _fetch(ssh_command, BADSSL_VALID) == 0, (
        f"a valid certificate ({BADSSL_VALID}) was rejected"
    )


@pytest.mark.parametrize("case", sorted(BADSSL_INVALID))
def test_tls_rejects_invalid_certificate(online, ssh_command, case):
    """The DUT refuses an HTTPS fetch when the certificate is invalid
    (untrusted CA, wrong hostname, expired). Accepting one means TLS
    verification is off, so HTTPS gives no authenticity."""
    assert _fetch(ssh_command, BADSSL_INVALID[case]) != 0, (
        f"an invalid certificate ({case}, {BADSSL_INVALID[case]}) was accepted: "
        "TLS verification is not working"
    )
