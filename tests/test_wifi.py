import re
import time

import pytest

# Bands and how to test them: the target's wifi-<band> features say which
# bands the device has. Fixed channels without radar detection (DFS), so an
# AP comes up right away; 6 GHz allows only SAE (WPA3).
WIFI_BANDS = {
    "2g": {"channel": "1", "mhz": (2400, 2500), "encryptions": ["psk2", "sae"]},
    "5g": {"channel": "36", "mhz": (5150, 5895), "encryptions": ["psk2", "sae"]},
    "6g": {"channel": "37", "mhz": (5925, 7125), "encryptions": ["sae"]},
}

# How iwinfo shows each encryption (it has rendered SAE as "WPA3 SAE (CCMP)",
# "SAE (CCMP)" and "SAE / SAE-EXT-KEY (CCMP)")
IWINFO_ENCRYPTION = {
    "psk2": r"Encryption: (WPA2 PSK|WPA-PSK) \(CCMP\)",
    "sae": r"Encryption:.*\bSAE\b.*\(CCMP\)",
}


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


def wifi_radio_for_band(ssh_command, band):
    """The radio (wireless.radioN) whose band is `band`, and its default
    interface section, or (None, None)."""
    config = "\n".join(ssh_command.run("uci show wireless")[0])
    radios = re.findall(rf"^wireless\.(\w+)\.band='{band}'$", config, re.MULTILINE)
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


def open_ap(ssh_command, band, encryption):
    """Turn on the `band` radio with an AP (SSID OpenWrt-CI-<band>) and wait
    until it runs; returns its interface. Fails if the device has no radio
    for the band (its target declares one) or the AP doesn't come up."""
    settings = WIFI_BANDS[band]
    ssid = f"OpenWrt-CI-{band}"
    radio, iface = wifi_radio_for_band(ssh_command, band)
    assert radio, f"the target declares wifi-{band}, but the device has no {band} radio"
    assert iface, f"no wifi-iface for {radio}"

    for setting in (
        f"uci delete wireless.{radio}.disabled",
        f"uci delete wireless.{iface}.disabled",
        f"uci set wireless.{radio}.channel={settings['channel']}",
        f"uci set wireless.{iface}.mode=ap",
        f"uci set wireless.{iface}.ssid={ssid}",
        f"uci set wireless.{iface}.encryption={encryption}",
        f"uci set wireless.{iface}.key=openwrt4all",
    ):
        ssh_command.run(setting)
    ssh_command.run("uci commit wireless")
    ssh_command.run("wifi reload")

    deadline = time.monotonic() + 60
    ifname = freq = None
    while time.monotonic() < deadline:
        ifname, freq = find_ap(ssh_command, ssid)
        if freq:
            break
        time.sleep(2)

    iw = "\n".join(ssh_command.run("iw dev")[0])
    assert freq, f"no AP with SSID {ssid} after 60 s; iw dev:\n{iw}"
    low, high = settings["mhz"]
    assert low <= freq <= high, (
        f"{ssid} runs on {freq} MHz, not in the {band} band; iw dev:\n{iw}"
    )
    return ifname


@pytest.mark.parametrize("band,encryption", band_params(encryptions=True))
def test_wifi_ap(ssh_command, band, encryption):
    """
    Open an access point on the device's `band` radio with `encryption`
    (psk2: WPA2, sae: WPA3) and check it runs in that band and encryption.

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
    Scan on the device's `band` radio and expect at least one network (needs
    other access points in range: the lab's wifi-neighbors).
    """
    ifname = open_ap(ssh_command, band, WIFI_BANDS[band]["encryptions"][0])
    scan_results = "\n".join(ssh_command.run(f"iwinfo {ifname} scan")[0])
    assert "ESSID:" in scan_results, f"iwinfo {ifname} scan:\n{scan_results}"


@pytest.mark.lg_feature("hwsim")
def test_wifi_hwsim_sae_mixed(ssh_command):
    """
    Test wifi configuration.

    This test creates one AP and one station and checks if they can connect to each other.
    It sets up the wireless configuration using the `ssh_command` fixture and relies on the
    "hwsim" driver to create the virtual radios.
    """
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

    # wait till network reload finished
    assert "timed out" not in "\n".join(
        ssh_command.run("ubus -t 5 wait_for hostapd.phy0-ap0")[0]
    )

    assert "Mode: Master  Channel: 11 (2.462 GHz)" in "\n".join(
        ssh_command.run("iwinfo")[0]
    )

    # Wait till the client associated
    assert "auth" in "\n".join(
        ssh_command.run(
            "ubus -t 20 subscribe hostapd.phy0-ap0 | grep '\"auth\":' | while read line; do echo auth && killall ubus; done"
        )[0]
    )

    assert "Mode: Client  Channel: 11 (2.462 GHz)" in "\n".join(
        ssh_command.run("iwinfo")[0]
    )

    assert "expected throughput" in "\n".join(
        ssh_command.run("iwinfo phy0-ap0 assoclist")[0]
    )
    assert "expected throughput" in "\n".join(
        ssh_command.run("iwinfo phy1-sta0 assoclist")[0]
    )

    ssh_command.run("uci set wireless.default_radio1.encryption=psk2")
    assert "wireless.default_radio1.encryption='psk2'" in "\n".join(
        ssh_command.run("uci changes")[0]
    )
    ssh_command.run("uci commit")
    ssh_command.run("service network reload")

    # Wait till the wifi client is removed
    assert "disassoc" in "\n".join(
        ssh_command.run(
            "ubus -t 20 subscribe hostapd.phy0-ap0 | grep '\"disassoc\":' | while read line; do echo disassoc && killall ubus; done"
        )[0]
    )

    # wait till network reload finished
    assert "timed out" not in "\n".join(
        ssh_command.run("ubus -t 5 wait_for wpa_supplicant.phy1-sta0")[0]
    )

    assert "expected throughput" not in "\n".join(
        ssh_command.run("iwinfo phy0-ap0 assoclist")[0]
    )

    # Wait till the client associated
    assert "auth" in "\n".join(
        ssh_command.run(
            "ubus -t 20 subscribe hostapd.phy0-ap0 | grep '\"auth\":' | while read line; do echo auth && killall ubus; done"
        )[0]
    )

    assert "expected throughput" in "\n".join(
        ssh_command.run("iwinfo phy0-ap0 assoclist")[0]
    )
