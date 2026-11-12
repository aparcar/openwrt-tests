"""
The DUT's access point with a real phone: an iPhone joins with each
encryption, a wrong password keeps it out, a hidden SSID works, and it
comes back after a Wi-Fi restart.

test_wifi_clients.py covers the same ground with many wpa_supplicant
stations (ath9k). This module checks interoperability with Apple's Wi-Fi
stack, which picks WPA2 or WPA3 itself, uses PMF on its own terms and
roams and sleeps differently.

- The phone is a place of its own (role=iphone-probe). The `iphone_probe`
  fixture (iphone_probe_fixtures.py) reserves and locks it for this module.
  QEMU and labs without one skip.
- iPhone 7 or newer: 802.11ac, 2.4 and 5 GHz, WPA3. Each test runs per
  band the target
  declares (wifi-2g, wifi-5g).
- The phone joins through a configuration profile with encryption "Any",
  as when a user types the password, unless a test pins it.

Join and lease times go to record_property.
"""

# ruff: noqa: F811 (tests take the imported fixture as an argument)

import ipaddress
import time

import pytest
from iphone_probe_fixtures import (
    PHONE_BANDS,
    PhoneBed,
    iphone_probe,  # noqa: F401 (fixture)
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
# Seen with an iPhone 7 (iOS 15.8) on an IgniteNet SS-W2-AC2600 (QCA9984,
# 5 GHz): it joins sae-mixed with PMF, but on a WPA3-only AP its auto-join
# finds "no scan candidate network" and never tries, with EncryptionType
# WPA3 as well as Any. Cause unknown (iOS 15, or what hostapd's WPA3-only
# setup adds); not strict, so a pass shows up as XPASS.
KNOWN = {
    "sae": [
        pytest.mark.xfail(
            reason="iPhone 7 / iOS 15 doesn't consider a WPA3-only AP a "
            "join candidate (cause unknown)",
            strict=False,
        )
    ],
}


def per_band(values, known=None):
    """pytest params (band, value) per band the target and phone have;
    `known`: extra marks per value."""
    params = []
    for band_param in BANDS:
        band = band_param.values[0]
        for value in values:
            marks = [*band_param.marks, *(known or {}).get(value, [])]
            params.append(pytest.param(band, value, marks=marks, id=f"{band}-{value}"))
    return params


def assert_joined(bed, state):
    if not state["authorized"]:
        pytest.fail(
            f"iPhone {bed.mac} not authorized on {bed.ssid} after "
            f"{state['after_s']}s: {state}\n{bed.diagnose()}"
        )
    if not state["ip"]:
        pytest.fail(
            f"iPhone {bed.mac} authorized but without a DHCP lease after "
            f"{state['after_s']}s: {state}\n{bed.diagnose()}"
        )


def assert_ping(bed, ip, **kwargs):
    sent, received = bed.ping(ip, **kwargs)
    assert received, f"the DUT can't ping the iPhone at {ip}: 0 of {sent}"


@pytest.mark.parametrize("band,encryption", per_band(AP_ENCRYPTIONS, KNOWN))
def test_iphone_joins(
    ssh_command, iphone_probe, target, band, encryption, record_property
):
    """
    For each band and AP encryption (open, WPA2, WPA/WPA2 mixed, WPA2/WPA3
    mixed, WPA3), an iPhone joins, gets an address in the device's LAN and
    the device reaches it. If it fails, iPhones can't use the network with
    that security setting.

    On a WPA3-only AP the phone must use PMF (WPA3 requires it). On a
    WPA2/WPA3 mixed AP PMF is optional and is recorded: on there doesn't
    prove SAE, since a WPA2 client that supports PMF negotiates it too.
    The DUT can't tell which (no hostapd_cli in default images).
    """
    bed = PhoneBed.start(ssh_command, iphone_probe, target, band, encryption)
    state = bed.wait()
    record_property("iphone_join_s", state["after_s"])
    assert_joined(bed, state)
    assert ipaddress.ip_address(state["ip"]) in DUT_LAN, state
    assert state["ip"] != DUT_LAN_IP, state
    assert_ping(bed, state["ip"])
    if encryption == "sae":
        assert state["mfp"], f"WPA3 client without MFP: {bed.dut_client()}"
    if encryption == "sae-mixed":
        record_property("iphone_sae_mixed_pmf", state["mfp"])


@pytest.mark.parametrize("band,encryption", per_band(["psk2", "sae"]))
def test_iphone_wrong_key_rejected(ssh_command, iphone_probe, target, band, encryption):
    """
    For each band, an iPhone with the wrong password doesn't get in, with
    WPA2 and WPA3. If it fails, the network admits clients it should keep
    out, a security problem.
    """
    bed = PhoneBed.start(
        ssh_command, iphone_probe, target, band, encryption, sta_key=WRONG_KEY
    )
    time.sleep(REJECT_SECONDS)
    state = bed.state()
    assert not state["authorized"], (
        f"DUT authorized the iPhone with a wrong key: {state}"
    )
    assert not state["ip"], f"iPhone with a wrong key got a lease: {state}"


@pytest.mark.parametrize("band", BANDS)
def test_iphone_hidden_ssid(ssh_command, iphone_probe, target, band):
    """For each band, an iPhone configured for a hidden network joins an AP
    that hides its SSID. If it fails, hidden networks are unusable for
    iPhones."""
    bed = PhoneBed.start(
        ssh_command,
        iphone_probe,
        target,
        band,
        "psk2",
        options={"hidden": 1},
        hidden=True,
    )
    state = bed.wait()
    assert_joined(bed, state)
    assert_ping(bed, state["ip"])


@pytest.mark.parametrize("band", BANDS)
def test_iphone_reconnects_after_wifi_restart(
    ssh_command, iphone_probe, target, band, record_property
):
    """For each band, after `wifi down` and `wifi up` the iPhone comes back
    on its own and the device reaches it again. If it fails, phones stay
    offline after a Wi-Fi restart until the user rejoins by hand. The
    reconnect time is recorded."""
    bed = PhoneBed.start(ssh_command, iphone_probe, target, band, "psk2")
    assert_joined(bed, bed.wait())

    ssh_command.run_check("wifi down")
    gone = bed.wait_gone()
    assert not gone["associated"], f"iPhone still associated after wifi down: {gone}"
    started = time.monotonic()
    ssh_command.run_check("wifi up")

    state = bed.wait(timeout=180)
    record_property("iphone_reconnect_s", round(time.monotonic() - started, 1))
    assert_joined(bed, state)
    assert_ping(bed, state["ip"])
