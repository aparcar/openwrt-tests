"""Wi-Fi on the device's own radios: access points per band and encryption,
scanning, and (on QEMU with mac80211_hwsim) an AP and a station connecting.

The helpers here (open_ap, band_params, wifi_reset) are shared with
test_wifi_clients.py."""

import json
import logging
import re
import time

import pytest
from labgrid.driver.exception import ExecutionError

logger = logging.getLogger(__name__)

# Bands and how to test them: the target's wifi-<band> features say which
# bands the device has. Fixed channels without radar detection (DFS), so an
# AP comes up right away; 6 GHz allows only SAE (WPA3).
WIFI_BANDS = {
    "2g": {"channel": "1", "mhz": (2400, 2500), "encryptions": ["psk2", "sae"]},
    "5g": {"channel": "36", "mhz": (5150, 5895), "encryptions": ["psk2", "sae"]},
    "6g": {"channel": "37", "mhz": (5925, 7125), "encryptions": ["sae"]},
}

# How iwinfo shows each encryption (it has rendered SAE as "WPA3 SAE (CCMP)",
# "SAE (CCMP)" and "SAE / SAE-EXT-KEY (CCMP)"). The ucode iwinfo (25.12)
# prints hostapd's key management and pairwise ciphers, "WPA-PSK (CCMP)",
# with no WPA/WPA2 distinction: psk-mixed only differs from psk2 in the
# ciphers, which depend on the cipher option, so it matches any PSK.
IWINFO_ENCRYPTION = {
    "none": r"Encryption: none",
    # With PMF on (ieee80211w) OpenWrt adds WPA-PSK-SHA256
    "psk2": r"Encryption: (WPA2 PSK|WPA-PSK)( / WPA-PSK-SHA256)? \(CCMP\)",
    "psk-mixed": r"Encryption: (mixed WPA/WPA2 PSK|WPA2? PSK|WPA-PSK) \(",
    "sae": r"Encryption:.*\bSAE\b.*\(CCMP\)",
    "sae-mixed": r"Encryption:.*(PSK.*\bSAE\b|\bSAE\b.*PSK)",
}

AP_KEY = "openwrt4all"
# wifi-iface options open_ap(options=...) may set. Every open_ap() clears
# them first, so they don't carry over from an earlier test.
AP_OPTIONS = ("hidden", "isolate", "maxassoc", "sae_pwe", "ieee80211w", "beacon_prot")
WIRELESS_BACKUP = "/tmp/wireless.pre-test"
# How long wifi_reset waits for netifd to finish bringing the radios down/up
WIFI_SETTLE_TIMEOUT = 30


@pytest.fixture(autouse=True)
def wifi_reset(request, ssh_command):
    """Every Wi-Fi test starts with the radios as the test before it found
    them (the boot default) and with fresh APs: afterwards the wireless config
    goes back and `wifi` takes every radio down and up again, so hostapd
    sets up each BSS anew. Otherwise the next open_ap() reconfigures the
    running BSS in place, and whatever survives that (the group keys of a
    WPA BSS turned open, test_wifi_client_open_after_wpa) makes the tests
    depend on their order. Also used by test_wifi_clients.py (imported)."""
    # hwsim creates /etc/config/wireless, and autouse fixtures run first
    if "hwsim" in request.fixturenames:
        request.getfixturevalue("hwsim")
    ssh_command.run_check(f"cp /etc/config/wireless {WIRELESS_BACKUP}")
    yield
    for cmd in (
        "uci revert wireless",
        f"cp {WIRELESS_BACKUP} /etc/config/wireless",
        "wifi",
    ):
        try:
            ssh_command.run(cmd, timeout=60)
        except (ExecutionError, OSError) as e:  # the next test will show it
            logger.warning("wifi reset: %s failed: %s", cmd, e)
            return
    wifi_settle(ssh_command)


def wifi_settle(ssh_command, timeout=WIFI_SETTLE_TIMEOUT):
    """Wait until netifd has no radio setup or teardown pending."""
    deadline = time.monotonic() + timeout
    while True:
        time.sleep(1)
        stdout, _, rc = ssh_command.run("wifi status")
        try:
            devices = json.loads("\n".join(stdout)) if rc == 0 else {}
        except json.JSONDecodeError:
            devices = {}
        pending = [name for name, dev in devices.items() if dev.get("pending")]
        if not pending:
            return
        if time.monotonic() > deadline:
            logger.warning("wifi reset: radios still pending: %s", pending)
            return


def band_params(*extra_features, encryptions=False):
    """pytest params per band (and encryption), each needing wifi-<band>
    plus `extra_features`"""
    params = []
    for band, settings in WIFI_BANDS.items():
        marks = pytest.mark.lg_feature([f"wifi-{band}", *extra_features])
        if encryptions:
            params += [
                pytest.param(band, enc, marks=marks, id=f"{band}-{enc}")
                for enc in settings["encryptions"]
            ]
        else:
            params.append(pytest.param(band, marks=marks, id=band))
    return params


def radio_supports_band(ssh_command, radio, band):
    """Whether the phy of `radio` has a channel in `band`.

    shortcut: checks the whole phy, so on a multi-radio wiphy (one phy, several
    wifi-devices with `radio`) it may pick the wrong one; check per-radio then."""
    phy = ssh_command.run(f"iwinfo nl80211 phyname {radio}")[0]
    if not phy:
        return False
    info = "\n".join(ssh_command.run(f"iw phy {phy[0].strip()} info")[0])
    low, high = WIFI_BANDS[band]["mhz"]
    return any(
        low <= float(mhz) <= high for mhz in re.findall(r"\* ([\d.]+) MHz", info)
    )


def wifi_radio_for_band(ssh_command, band):
    """The radio (wireless.radioN) whose band is `band`, and its default
    interface section, or (None, None).

    If no radio is configured for `band`, a radio whose hardware supports it:
    dual-band radios default to their highest band (5g), so a device with two
    of them has no 2g radio until one is switched (open_ap sets the band)."""
    config = "\n".join(ssh_command.run("uci show wireless")[0])
    radios = re.findall(rf"^wireless\.(\w+)\.band='{band}'$", config, re.MULTILINE)
    if not radios:
        devices = re.findall(r"^wireless\.(\w+)=wifi-device$", config, re.MULTILINE)
        radios = [r for r in devices if radio_supports_band(ssh_command, r, band)]
    if not radios:
        return None, None
    radio = radios[0]
    ifaces = re.findall(rf"^wireless\.(\w+)\.device='{radio}'$", config, re.MULTILINE)
    return radio, (ifaces[0] if ifaces else None)


def find_ap(ssh_command, ssid):
    """(interface, frequency in MHz) of the AP with `ssid`, or (None, None)."""
    iw = "\n".join(ssh_command.run("iw dev")[0])
    for block in re.split(r"\n\s*Interface ", "\n" + iw):
        if re.search(
            rf"^\s*ssid {re.escape(ssid)}$", block, re.MULTILINE
        ) and re.search(r"type AP", block):
            freq = re.search(r"channel \d+ \((\d+) MHz\)", block)
            return block.split()[0], (int(freq.group(1)) if freq else None)
    return None, None


def wifi_diagnostics(ssh_command):
    """The radios the kernel has and the last driver and hostapd messages,
    for failure messages: the console doesn't show them (dmesg -n 1)."""
    out = ssh_command.run(
        "ls /sys/class/ieee80211; "
        "dmesg | grep -i -E 'mt7|ath1|eeprom|firmware|ieee80211|phy[0-9]' | tail -n 20; "
        "logread -e hostapd | tail -n 20"
    )[0]
    return "\n".join(out)


def open_ap(
    ssh_command, band, encryption, ssid=None, key=AP_KEY, options=None, channel=None
):
    """Turn on the `band` radio with an AP (SSID `ssid`, default
    OpenWrt-CI-<band>) and wait until it runs with `encryption`; returns its
    interface. Fails if the device has no radio for the band (its target
    declares one) or the AP doesn't come up.

    options: AP_OPTIONS to set on the wifi-iface ({"isolate": 1}); channel:
    instead of the band's default.

    `wifi reload` returns before the new config is applied, and a previous
    test may have left an AP with the same SSID (other encryption) running,
    so wait for the encryption too, not just the SSID."""
    settings = WIFI_BANDS[band]
    ssid = ssid or f"OpenWrt-CI-{band}"
    radio, iface = wifi_radio_for_band(ssh_command, band)
    assert radio, (
        f"the target declares wifi-{band}, but the device has no {band} radio\n"
        f"{wifi_diagnostics(ssh_command)}"
    )
    assert iface, f"no wifi-iface for {radio}"

    settings_cmds = [
        f"uci delete wireless.{radio}.disabled",
        f"uci delete wireless.{iface}.disabled",
        f"uci set wireless.{radio}.band={band}",
        f"uci set wireless.{radio}.channel={channel or settings['channel']}",
        f"uci set wireless.{iface}.mode=ap",
        f"uci set wireless.{iface}.ssid={ssid}",
        f"uci set wireless.{iface}.encryption={encryption}",
        f"uci -q delete wireless.{iface}.key"
        if encryption == "none"
        else f"uci set wireless.{iface}.key={key}",
    ]
    settings_cmds += [f"uci -q delete wireless.{iface}.{opt}" for opt in AP_OPTIONS]
    for opt, value in (options or {}).items():
        assert opt in AP_OPTIONS, f"open_ap: unknown option {opt}"
        settings_cmds.append(f"uci set wireless.{iface}.{opt}={value}")
    for setting in settings_cmds:
        ssh_command.run(setting)
    ssh_command.run("uci commit wireless")
    ssh_command.run("wifi reload")

    deadline = time.monotonic() + 60
    ifname = freq = None
    info = ""
    while time.monotonic() < deadline:
        ifname, freq = find_ap(ssh_command, ssid)
        if freq:
            info = "\n".join(ssh_command.run(f"iwinfo {ifname} info")[0])
            if re.search(IWINFO_ENCRYPTION[encryption], info):
                break
        time.sleep(2)

    iw = "\n".join(ssh_command.run("iw dev")[0])
    assert freq, (
        f"no AP with SSID {ssid} after 60 s; iw dev:\n{iw}\n"
        f"{wifi_diagnostics(ssh_command)}"
    )
    assert re.search(IWINFO_ENCRYPTION[encryption], info), (
        f"{ssid} not running with {encryption} after 60 s; "
        f"iwinfo {ifname} info:\n{info}"
    )
    low, high = settings["mhz"]
    assert low <= freq <= high, (
        f"{ssid} runs on {freq} MHz, not in the {band} band; iw dev:\n{iw}"
    )
    return ifname


@pytest.mark.parametrize("band,encryption", band_params(encryptions=True))
def test_wifi_ap(ssh_command, band, encryption):
    """
    For each band and encryption the device supports, an access point
    (psk2: WPA2, sae: WPA3) comes up in that band with that encryption. If it
    fails, the device can't offer a working Wi-Fi network as configured.

    Runs only on targets with the matching feature (wifi-2g, wifi-5g,
    wifi-6g); fails if the device has no radio for a band its target file
    declares.
    """
    ifname = open_ap(ssh_command, band, encryption)
    info = "\n".join(ssh_command.run(f"iwinfo {ifname} info")[0])
    assert "Mode: Master" in info, f"iwinfo {ifname} info:\n{info}"
    assert re.search(IWINFO_ENCRYPTION[encryption], info), (
        f"iwinfo {ifname} info:\n{info}"
    )


@pytest.mark.parametrize("band", band_params("wifi-neighbors"))
def test_wifi_scan(ssh_command, band):
    """
    For each band, a scan on the device's radio finds at least one other
    network. If it fails, the radio can't see its surroundings, so client
    mode and channel selection can't work either.

    Needs other access points in range (the lab's wifi-neighbors feature).
    """
    ifname = open_ap(ssh_command, band, WIFI_BANDS[band]["encryptions"][0])
    scan_results = "\n".join(ssh_command.run(f"iwinfo {ifname} scan")[0])
    assert "ESSID:" in scan_results, f"iwinfo {ifname} scan:\n{scan_results}"


@pytest.mark.lg_feature("hwsim")
def test_wifi_hwsim_sae_mixed(hwsim, ssh_command):
    """
    A station joins an AP set to WPA2/WPA3 mixed mode (sae-mixed), and after
    switching the station to WPA2 only (psk2) it reconnects. If it fails,
    mixed-mode networks lock out clients, e.g. older WPA2-only devices.

    One AP and one station on two simulated radios of the same device. It
    sets up the wireless configuration using the `ssh_command` fixture and relies on the
    "hwsim" driver to create the virtual radios (installed by the `hwsim` fixture).
    """
    # `wifi config` makes the all-band hwsim radios 6g with country '00',
    # which hostapd rejects ("Invalid country_code '00'")
    ssh_command.run("uci set wireless.radio0.country=US")
    ssh_command.run("uci set wireless.radio1.country=US")
    ssh_command.run("uci set wireless.radio0.channel=11")
    ssh_command.run("uci set wireless.radio0.band=2g")
    ssh_command.run("uci delete wireless.default_radio0.disabled")
    ssh_command.run("uci delete wireless.radio0.disabled")

    ssh_command.run("uci set wireless.default_radio0.encryption=sae-mixed")
    ssh_command.run("uci set wireless.default_radio0.key=testtest")

    ssh_command.run("uci delete wireless.radio1.channel")
    ssh_command.run("uci set wireless.radio1.band=2g")
    ssh_command.run("uci delete wireless.default_radio1.disabled")
    ssh_command.run("uci delete wireless.radio1.disabled")

    ssh_command.run("uci set wireless.default_radio1.network=wan")
    ssh_command.run("uci set wireless.default_radio1.mode=sta")
    ssh_command.run("uci set wireless.default_radio1.encryption=sae-mixed")
    ssh_command.run("uci set wireless.default_radio1.key=testtest")

    changes = "\n".join(ssh_command.run("uci changes")[0])
    assert (
        "-wireless.default_radio1.disabled" in changes
        or "-wireless.radio1.disabled" in changes
    )

    ssh_command.run("uci commit")
    ssh_command.run("service network reload")

    # hwsim reloads (test_wifi_hwsim.py) renumber the phys
    ap = ssh_command.run_check("iwinfo nl80211 phyname radio0")[0] + "-ap0"
    sta = ssh_command.run_check("iwinfo nl80211 phyname radio1")[0] + "-sta0"

    # Poll instead of `ubus subscribe`: the AP comes up after its hostapd
    # object (HT_SCAN), and the station may join before a subscription starts
    def wait_for(cmd, text, timeout=60):
        deadline = time.monotonic() + timeout
        out = ""
        while text not in out and time.monotonic() < deadline:
            time.sleep(2)
            out = "\n".join(ssh_command.run(cmd)[0])
        return out

    def debug():
        return "\n".join(
            f"--- {cmd}\n" + "\n".join(ssh_command.run(cmd)[0])
            for cmd in ("iw dev", "uci show wireless", "logread | tail -n 40")
        )

    assert "Mode: Master  Channel: 11 (2.462 GHz)" in wait_for(
        "iwinfo", "Master  Channel: 11 ("
    ), debug()
    assert "expected throughput" in wait_for(
        f"iwinfo {ap} assoclist", "expected throughput"
    ), debug()
    assert "Mode: Client  Channel: 11 (2.462 GHz)" in "\n".join(
        ssh_command.run("iwinfo")[0]
    )
    assert "expected throughput" in "\n".join(
        ssh_command.run(f"iwinfo {sta} assoclist")[0]
    )

    ssh_command.run("uci set wireless.default_radio1.encryption=psk2")
    assert "wireless.default_radio1.encryption='psk2'" in "\n".join(
        ssh_command.run("uci changes")[0]
    )
    token = f"sae-mixed-{time.monotonic_ns()}"
    ssh_command.run(f"logger -t wifi-test {token}")
    ssh_command.run("uci commit")
    ssh_command.run("service network reload")

    # The station leaves and joins again, now with WPA2 only
    since = f"logread | sed -n '/{token}/,$p' | grep {ap}"
    log = wait_for(since, "AP-STA-DISCONNECTED")
    assert "AP-STA-DISCONNECTED" in log, debug()
    log = wait_for(f"{since} | sed -n '/AP-STA-DISCONNECTED/,$p'", "AP-STA-CONNECTED")
    assert "AP-STA-CONNECTED" in log, debug()
    assert "expected throughput" in wait_for(
        f"iwinfo {ap} assoclist", "expected throughput"
    ), debug()
