"""Fixtures for the lab's Wi-Fi probe (contrib/labgrid-probe, "Wi-Fi probe").

`wifi_probe`: the probe's LabgridWifiProbeDriver, reserved and locked for the
test module that uses it, like `probe` (probe_fixtures.py): a place of its
own, labgrid-<lab>-wifi-probe, tag role=wifi-probe (labnet.yaml
`wifi_probe: true`). Skips on QEMU and in labs without one. Its stations are
removed before and after the module.

`WifiBed`: one AP on the DUT plus N probe stations on it:

    from wifi_probe_fixtures import WifiBed, wifi_probe  # noqa: F401

    bed = WifiBed.start(ssh_command, wifi_probe, target, "2g", "psk2", count=8)
    status = bed.wait()          # all connected and leased (or the timeout)
    bed.ping()                   # every station pings the DUT
    bed.dut_clients()            # hostapd get_clients on the DUT

The AP's SSID comes from the DUT's place and a counter: unique per DUT, so
the stations don't join another DUT's AP in range, and per start(), so
open_ap() returns only once this configuration is on the air. The
stations are also locked to the AP's BSSID.
"""

import hashlib
import itertools
import logging
import re
import time
from dataclasses import dataclass

import pytest
from conftest import ubus_call
from probe_fixtures import DUT_LAN_IP, ProbeError, hold_probe_place
from test_wifi import AP_KEY, find_ap, open_ap

logger = logging.getLogger(__name__)

WIFI_PROBE_TAGS = {"role": "wifi-probe"}
# The probe's radios are ath9k: 802.11n, no 6 GHz
PROBE_BANDS = ("2g", "5g")
ASSOC_TIMEOUT = 180

# The lab host has an address in the DUT's LAN on each DUT VLAN,
# 192.168.1.<VLAN id> (.102 on vlan102), inside dnsmasq's default pool
# (.100-.249). Once dnsmasq offers that address to a probe station, the DUT's
# ARP entry for it points to the station: the lab host's SSH connection to the
# DUT dies, and the station can't reach the DUT. So the stations' pool starts
# above the lab's VLAN ids.
DHCP_POOL_START = 150
DHCP_POOL_LIMIT = 100

_ssid_counter = itertools.count()


def dut_dhcp_pool(ssh_command):
    """Move the DUT's LAN DHCP pool to .DHCP_POOL_START and up (once)."""
    start = ssh_command.run("uci -q get dhcp.lan.start")[0]
    if start == [str(DHCP_POOL_START)]:
        return
    ssh_command.run_check(f"uci set dhcp.lan.start={DHCP_POOL_START}")
    ssh_command.run_check(f"uci set dhcp.lan.limit={DHCP_POOL_LIMIT}")
    ssh_command.run_check("uci commit dhcp")
    ssh_command.run_check("/etc/init.d/dnsmasq restart")
    # dnsmasq answers DHCP again only once it has re-read its config
    time.sleep(3)


@pytest.fixture(scope="module")
def wifi_probe(env, target, strategy):
    """The Wi-Fi probe's LabgridWifiProbeDriver, locked for the module.

    Boots the DUT first: being module-scoped, this fixture is set up before
    the tests' function-scoped shell_command/ssh_command, and the probe is
    shared by the lab, so it shouldn't wait out a boot reserved."""
    strategy.transition("shell")
    for driver in hold_probe_place(
        env, target, WIFI_PROBE_TAGS, "LabgridWifiProbeDriver", "wifi-probe"
    ):
        # Leftovers of a run that was killed before its teardown
        driver.sta_down()
        try:
            yield driver
        finally:
            try:
                driver.sta_down()
            except ProbeError as e:
                logger.warning("removing the Wi-Fi probe's stations failed: %s", e)


def bed_ssid(target, band):
    place = target.get_resource("RemotePlace").name
    tag = hashlib.sha1(place.encode()).hexdigest()[:6]
    return f"owci-{tag}-{band}-{next(_ssid_counter)}"


def dut_mem_available_kb(ssh_command):
    meminfo = "\n".join(ssh_command.run_check("cat /proc/meminfo"))
    return int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1))


def not_connected(status):
    return [s["ifname"] for s in status["stations"] if not s["connected"]]


def not_leased(status):
    return [s["ifname"] for s in status["stations"] if not s["ip"]]


def summary(status):
    """A short text for assertion messages."""
    keys = (
        "phase",
        "error",
        "count",
        "connected",
        "leases",
        "first_connected_s",
        "all_connected_s",
        "all_leases_s",
    )
    return ", ".join(f"{k}={status.get(k)}" for k in keys if status.get(k) is not None)


@dataclass
class WifiBed:
    ssh: object
    probe: object
    band: str
    ssid: str
    encryption: str
    bssid: str
    freq: int
    count: int

    @classmethod
    def start(
        cls,
        ssh_command,
        probe,
        target,
        band,
        encryption="psk2",
        count=1,
        sta_encryption=None,
        key=AP_KEY,
        sta_key=None,
        options=None,
        channel=None,
        lock_freq=True,
        hidden=False,
    ):
        """Open an AP on the DUT (open_ap(): `encryption`, `key`, `options`,
        `channel`) and start `count` probe stations on it, with
        `sta_encryption`/`sta_key` (default: the AP's). Returns at once;
        wait() for them. lock_freq: the stations only look on the AP's
        current channel."""
        if band not in probe.bands():
            pytest.skip(f"the Wi-Fi probe has no {band} radio")
        dut_dhcp_pool(ssh_command)
        ssid = bed_ssid(target, band)
        ifname = open_ap(
            ssh_command,
            band,
            encryption,
            ssid=ssid,
            key=key,
            options=options,
            channel=channel,
        )
        bssid = ssh_command.run_check(f"cat /sys/class/net/{ifname}/address")[0]
        _, freq = find_ap(ssh_command, ssid)
        sta_encryption = sta_encryption or encryption
        probe.sta_down(band)
        probe.sta_up(
            band,
            count,
            ssid,
            bssid=bssid.strip().lower(),
            freq=freq if lock_freq else None,
            encryption=sta_encryption,
            key=None if sta_encryption == "none" else (sta_key or key),
            hidden=hidden or None,
        )
        return cls(ssh_command, probe, band, ssid, encryption, bssid, freq, count)

    def status(self, detail=False):
        return self.probe.sta_status(self.band, detail=detail)

    def wait(self, count=None, leases=True, timeout=ASSOC_TIMEOUT):
        return self.probe.wait_stations(
            self.band, count=count, leases=leases, timeout=timeout
        )

    def ping(self, host=DUT_LAN_IP, count=3, size=None, stations=None, diag=None):
        return self.probe.sta_ping(
            self.band, host, count=count, size=size, stations=stations, diag=diag
        )

    def ap_ifname(self):
        ifname, _ = find_ap(self.ssh, self.ssid)
        assert ifname, f"no AP {self.ssid} on the DUT"
        return ifname

    def dut_clients(self):
        """hostapd's clients of the AP: {mac: {authorized, bytes, packets, ...}}"""
        reply = ubus_call(self.ssh, f"hostapd.{self.ap_ifname()}", "get_clients")
        return {mac.lower(): info for mac, info in reply.get("clients", {}).items()}

    def dut_authorized(self):
        return {
            mac for mac, info in self.dut_clients().items() if info.get("authorized")
        }

    def wait_connected_below(self, count, timeout=30):
        """Wait until fewer than `count` stations are connected."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.status()
            if status["connected"] < count or time.monotonic() > deadline:
                return status
            time.sleep(2)
