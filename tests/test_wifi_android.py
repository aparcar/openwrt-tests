"""
The DUT's access point with a real Android phone: it joins with each
encryption, a wrong password keeps it out, a hidden SSID works, and it
comes back after a Wi-Fi restart.

test_wifi_clients.py covers the same ground with many wpa_supplicant
stations (ath9k). This module checks interoperability with Android's Wi-Fi
stack, which picks WPA2 or WPA3 itself, uses PMF on its own terms and
roams and sleeps differently.

- The phone is a place of its own (role=android-probe). The
  `android_probe` fixture (android_probe_fixtures.py) reserves and locks it
  for this module. QEMU and labs without one skip.
- Android 12 or newer: 2.4 and 5 GHz, WPA3. Each test runs per band the
  target declares (wifi-2g, wifi-5g).
- The phone joins as a user typing the password would (WPA2, which
  Android upgrades to WPA3 on a transition AP; WPA3 on a WPA3-only AP),
  unless a test pins it.

Join and lease times go to record_property.
"""

# ruff: noqa: F811 (tests take the imported fixture as an argument)

import ipaddress
import time

import pytest
from android_probe_fixtures import (
    PHONE_BANDS,
    PhoneBed,
    android_probe,  # noqa: F401 (fixture)
)
from probe_fixtures import DUT_LAN, DUT_LAN_IP
from test_wifi import (
    band_params,
    wifi_reset,  # noqa: F401 (autouse fixture: fresh APs for every test)
)

BANDS = [p for p in band_params() if p.values[0] in PHONE_BANDS]
# How long the phone gets to (not) get in with the wrong password
REJECT_SECONDS = 45
WRONG_KEY = "not-the-key-42"
AP_ENCRYPTIONS = ["none", "psk2", "psk-mixed", "sae-mixed", "sae"]
# Seen on the OpenWrt One, WDR3600, SS-W2-AC2600 and Pulse EX400 (Pixel 5,
# Android 14, screen off): after `wifi down`/`wifi up` the AP is back on
# channel 36, the lab's Linux clients rejoin within seconds
# (test_wifi_restart_clients_reconnect[5g]), the phone doesn't within 180 s.
# Not strict, so a pass shows up as XPASS.
RESTART_5G_XFAIL = pytest.mark.xfail(
    reason="the phone doesn't rescan for a 5 GHz AP that vanished; Linux "
    "clients do rejoin",
    strict=False,
)
RESTART_BANDS = [
    pytest.param(
        *p.values,
        marks=[*p.marks, RESTART_5G_XFAIL] if p.values[0] == "5g" else p.marks,
        id=p.id,
    )
    for p in BANDS
]


def per_band(values):
    """pytest params (band, value) per band the target and phone have"""
    return [
        pytest.param(
            band_param.values[0],
            value,
            marks=band_param.marks,
            id=f"{band_param.values[0]}-{value}",
        )
        for band_param in BANDS
        for value in values
    ]


def assert_joined(bed, state):
    if not state["authorized"]:
        pytest.fail(
            f"Android phone {bed.mac} not authorized on {bed.ssid} after "
            f"{state['after_s']}s: {state}\n{bed.diagnose()}"
        )
    if not state["ip"]:
        pytest.fail(
            f"Android phone {bed.mac} authorized but without a DHCP lease "
            f"after {state['after_s']}s: {state}\n{bed.diagnose()}"
        )


def assert_ping(bed, ip, **kwargs):
    sent, received = bed.ping(ip, **kwargs)
    assert received, f"the DUT can't ping the Android phone at {ip}: 0 of {sent}"


@pytest.mark.parametrize("band,encryption", per_band(AP_ENCRYPTIONS))
def test_android_joins(
    ssh_command, android_probe, target, band, encryption, record_property
):
    """
    For each band and AP encryption (open, WPA2, WPA/WPA2 mixed, WPA2/WPA3
    mixed, WPA3), an Android phone joins, gets an address in the device's
    LAN and the device reaches it. If it fails, Android phones can't use the
    network with that security setting.

    On a WPA3-only AP the phone must use PMF (WPA3 requires it). On a
    WPA2/WPA3 mixed AP PMF is optional and is recorded: on there doesn't
    prove SAE, since a WPA2 client that supports PMF negotiates it too.
    """
    bed = PhoneBed.start(ssh_command, android_probe, target, band, encryption)
    state = bed.wait()
    record_property("android_join_s", state["after_s"])
    assert_joined(bed, state)
    assert ipaddress.ip_address(state["ip"]) in DUT_LAN, state
    assert state["ip"] != DUT_LAN_IP, state
    assert_ping(bed, state["ip"])
    if encryption == "sae":
        assert state["mfp"], f"WPA3 client without MFP: {bed.dut_client()}"
    if encryption == "sae-mixed":
        record_property("android_sae_mixed_pmf", state["mfp"])


@pytest.mark.parametrize("band", BANDS)
def test_android_joins_wpa3_without_h2e(
    ssh_command, android_probe, target, band, record_property
):
    """
    For each band, an Android phone joins a WPA3-only AP with SAE-H2E off
    (sae_pwe=0, hunting and pecking only), gets an address and the device
    reaches it. If it fails, the setting OpenWrt offers for clients that
    trip over its H2E default (sae_pwe=2, openwrt commit 70ba7512) locks
    Android phones out instead.
    """
    bed = PhoneBed.start(
        ssh_command, android_probe, target, band, "sae", options={"sae_pwe": 0}
    )
    # An image whose wifi-scripts ignore uci sae_pwe would test the default
    hostapd = ssh_command.run_check("grep -h '^sae_pwe=' /var/run/hostapd-*.conf")
    assert "sae_pwe=0" in hostapd, f"sae_pwe not applied: {hostapd}"
    state = bed.wait()
    record_property("android_join_s", state["after_s"])
    assert_joined(bed, state)
    assert_ping(bed, state["ip"])
    assert state["mfp"], f"WPA3 client without MFP: {bed.dut_client()}"


@pytest.mark.parametrize("band,encryption", per_band(["psk2", "sae"]))
def test_android_wrong_key_rejected(
    ssh_command, android_probe, target, band, encryption
):
    """
    For each band, an Android phone with the wrong password doesn't get in,
    with WPA2 and WPA3. If it fails, the network admits clients it should
    keep out, a security problem.
    """
    bed = PhoneBed.start(
        ssh_command, android_probe, target, band, encryption, sta_key=WRONG_KEY
    )
    time.sleep(REJECT_SECONDS)
    state = bed.state()
    assert not state["authorized"], (
        f"DUT authorized the Android phone with a wrong key: {state}"
    )
    assert not state["ip"], f"Android phone with a wrong key got a lease: {state}"


@pytest.mark.parametrize("band", BANDS)
def test_android_hidden_ssid(ssh_command, android_probe, target, band):
    """For each band, an Android phone configured for a hidden network joins
    an AP that hides its SSID. If it fails, hidden networks are unusable for
    Android phones."""
    bed = PhoneBed.start(
        ssh_command,
        android_probe,
        target,
        band,
        "psk2",
        options={"hidden": 1},
        hidden=True,
    )
    state = bed.wait()
    assert_joined(bed, state)
    assert_ping(bed, state["ip"])


@pytest.mark.parametrize("band", RESTART_BANDS)
def test_android_reconnects_after_wifi_restart(
    ssh_command, android_probe, target, band, record_property
):
    """For each band, after `wifi down` and `wifi up` the Android phone comes
    back on its own and the device reaches it again. If it fails, phones stay
    offline after a Wi-Fi restart until the user rejoins by hand. The
    reconnect time is recorded."""
    bed = PhoneBed.start(ssh_command, android_probe, target, band, "psk2")
    assert_joined(bed, bed.wait())

    ssh_command.run_check("wifi down")
    gone = bed.wait_gone()
    assert not gone["associated"], (
        f"Android phone still associated after wifi down: {gone}"
    )
    started = time.monotonic()
    ssh_command.run_check("wifi up")

    state = bed.wait(timeout=180)
    record_property("android_reconnect_s", round(time.monotonic() - started, 1))
    assert_joined(bed, state)
    # The phone may keep its lease without a new DHCP exchange, so nothing
    # on the DUT says when its address is back up: ping for ~20 s
    assert_ping(bed, state["ip"], count=20)
