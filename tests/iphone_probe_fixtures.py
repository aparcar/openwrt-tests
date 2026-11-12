"""Fixtures for the lab's iPhone Wi-Fi probe (contrib/iphone-probe,
docs/iphone-probe.md).

`iphone_probe`: the IPhoneProbeDriver, reserved and locked for the test
module that uses it, like `wifi_probe`: a place of its own,
labgrid-<lab>-iphone-probe, tag role=iphone-probe (labnet.yaml
`iphone_probe: true`). Skips on QEMU and in labs without one. The phone
leaves its network before and after the module.

`PhoneBed`: one AP on the DUT and the phone on it:

    from iphone_probe_fixtures import PhoneBed, iphone_probe  # noqa: F401

    bed = PhoneBed.start(ssh_command, iphone_probe, target, "5g", "sae")
    state = bed.wait()           # authorized and leased (or the timeout)
    bed.ping()                   # the DUT pings the phone

Unlike the ath9k probe's stations, the phone can't tell whether it is
associated or which address it got: both come from the DUT (hostapd's
clients, dnsmasq's leases), by the phone's hardware MAC (the profile turns
MAC randomization off). The phone has one radio for both bands; the SSID is
unique per band and start(), so it joins the band under test.
"""

import logging
import re
import sys
import time
from pathlib import Path

import pytest
from conftest import ubus_call
from labgrid.driver.exception import ExecutionError
from probe_fixtures import hold_probe_place
from test_wifi import AP_KEY, find_ap, open_ap
from wifi_probe_fixtures import bed_ssid, dut_dhcp_pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drivers.iphoneprobedriver import (  # noqa: F401 (registers the driver)
    IPhoneProbeDriver,
    IPhoneProbeError,
)

logger = logging.getLogger(__name__)

IPHONE_PROBE_TAGS = {"role": "iphone-probe"}
# iPhone 7 and newer: 802.11ac, 2.4 and 5 GHz, WPA3
PHONE_BANDS = ("2g", "5g")
# A phone scans less eagerly than wpa_supplicant: give it time
JOIN_TIMEOUT = 120
# How long diagnose() records the phone's Wi-Fi log: more than its longest
# auto-join retry interval (15 s), so it catches at least one attempt
PHONE_LOG_SECONDS = 20
# Below this the phone may stop answering; the lab should keep it charging
MIN_BATTERY_PERCENT = 20


@pytest.fixture(scope="module")
def iphone_probe(env, target, strategy):
    """The iPhone probe's IPhoneProbeDriver, locked for the module.

    Boots the DUT first (see wifi_probe). Fails if the phone isn't usable
    (not reachable, not supervised, battery low): the lab announced it."""
    strategy.transition("shell")
    with hold_probe_place(
        env,
        target,
        IPHONE_PROBE_TAGS,
        "IPhoneProbeDriver",
        "iphone-probe",
        needs=("SSHDriver",),
    ) as phone:
        info = phone.info()
        logger.info("iPhone probe: %s", info)
        if not info["supervised"]:
            pytest.fail(f"the iPhone probe is not supervised: {info}")
        battery = info.get("battery_percent")
        if battery is not None and battery < MIN_BATTERY_PERCENT:
            pytest.fail(f"the iPhone probe's battery is at {battery}%: {info}")
        # Leftovers of a run that was killed before its teardown
        phone.leave()
        try:
            yield phone
        finally:
            try:
                phone.leave()
            except IPhoneProbeError as e:
                logger.warning("the iPhone probe failed to leave: %s", e)


def dut_leases(ssh_command):
    """dnsmasq's leases on the DUT: {mac: ip}"""
    stdout, _, _ = ssh_command.run("cat /tmp/dhcp.leases")
    leases = {}
    for line in stdout:
        fields = line.split()
        if len(fields) >= 3:
            leases[fields[1].lower()] = fields[2]
    return leases


class PhoneBed:
    def __init__(self, ssh, phone, band, ssid, encryption, mac):
        self.ssh = ssh
        self.phone = phone
        self.band = band
        self.ssid = ssid
        self.encryption = encryption
        self.mac = mac
        self.started = time.monotonic()

    @classmethod
    def start(
        cls,
        ssh_command,
        phone,
        target,
        band,
        encryption="psk2",
        sta_encryption="any",
        key=AP_KEY,
        sta_key=None,
        options=None,
        hidden=False,
    ):
        """Open an AP on the DUT (open_ap(): `encryption`, `key`, `options`)
        and have the phone join it with `sta_encryption` (default "any": the
        phone picks, like a user typing the password) and `sta_key`
        (default: the AP's). Returns at once; wait() for it."""
        if band not in PHONE_BANDS:
            pytest.skip(f"the iPhone probe has no {band} radio")
        dut_dhcp_pool(ssh_command)
        ssid = bed_ssid(target, band)
        open_ap(ssh_command, band, encryption, ssid=ssid, key=key, options=options)
        if encryption == "none":
            sta_encryption = "none"
        reply = phone.join(
            ssid,
            sta_encryption,
            key=None if sta_encryption == "none" else (sta_key or key),
            hidden=hidden,
        )
        if reply.get("warning"):
            logger.warning("iprobe join: %s", reply["warning"])
        return cls(ssh_command, phone, band, ssid, encryption, reply["mac"])

    def ap_ifname(self):
        ifname, _ = find_ap(self.ssh, self.ssid)
        assert ifname, f"no AP {self.ssid} on the DUT"
        return ifname

    def dut_client(self):
        """hostapd's entry for the phone ({authorized, mfp, bytes, ...}), or
        None while it isn't associated (or the AP is down)."""
        ifname, _ = find_ap(self.ssh, self.ssid)
        if not ifname:
            return None
        try:
            reply = ubus_call(self.ssh, f"hostapd.{ifname}", "get_clients")
        except ExecutionError:  # hostapd went away in between (wifi down)
            return None
        clients = {m.lower(): c for m, c in reply.get("clients", {}).items()}
        return clients.get(self.mac)

    def state(self):
        client = self.dut_client()
        return {
            "mac": self.mac,
            "associated": client is not None,
            "authorized": bool(client and client.get("authorized")),
            "mfp": bool(client and client.get("mfp")),
            "ip": dut_leases(self.ssh).get(self.mac),
            "after_s": round(time.monotonic() - self.started, 1),
        }

    def wait(self, leases=True, timeout=JOIN_TIMEOUT, poll=2.0):
        """Wait until the DUT lists the phone as authorized (and leased).
        Returns the last state(), also on timeout: callers assert on it."""
        deadline = time.monotonic() + timeout
        while True:
            state = self.state()
            if state["authorized"] and (state["ip"] or not leases):
                return state
            if time.monotonic() > deadline:
                return state
            time.sleep(poll)

    def wait_gone(self, timeout=30, poll=2.0):
        """Wait until the phone is no longer associated; the last state()."""
        deadline = time.monotonic() + timeout
        while True:
            state = self.state()
            if not state["associated"] or time.monotonic() > deadline:
                return state
            time.sleep(poll)

    def ping(self, ip, count=3, size=None):
        """The DUT pings the phone: (sent, received)."""
        opts = f"-s {size} " if size else ""
        stdout, _, _ = self.ssh.run(f"ping -c {count} -W 2 {opts}{ip}", timeout=60)
        match = re.search(
            r"(\d+) packets transmitted, (\d+) (?:packets )?received", "\n".join(stdout)
        )
        return (int(match.group(1)), int(match.group(2))) if match else (count, 0)

    def diagnose(self):
        """The phone and the DUT's view of it, as text for a failure
        message: the phone's info and its Wi-Fi log for PHONE_LOG_SECONDS
        (why it doesn't join: it retries every 1-15 s), then the DUT's AP.
        Never raises."""
        out = []
        try:
            out.append(f"phone: {self.phone.info()}")
            lines = self.phone.log(PHONE_LOG_SECONDS)
            out.append(
                f"# phone wifid log, {PHONE_LOG_SECONDS}s, join decisions:\n"
                + ("\n".join(lines) or "(nothing)")
            )
        except IPhoneProbeError as e:
            out.append(f"phone info/log failed: {e}")
        ap = find_ap(self.ssh, self.ssid)[0] or "phy*-ap0"
        for cmd in (
            f"iw dev {ap} station dump",
            f"ubus call hostapd.{ap} get_clients",
            "grep -hvE '^#|^$' /var/run/hostapd-*.conf",
            f"logread | grep -iE 'hostapd|dnsmasq-dhcp|{self.mac}' | tail -n 40",
        ):
            try:
                stdout, stderr, rc = self.ssh.run(cmd, timeout=30)
                out.append(f"# {cmd} (rc {rc})\n" + "\n".join(stdout + stderr))
            except Exception as e:  # noqa: BLE001 (diagnostics only)
                out.append(f"# {cmd}: {e}")
        return "\n".join(out)
