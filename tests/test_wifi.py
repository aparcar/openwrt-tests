import time

import pytest


def restart_wifi_and_wait(ssh_command, timeout=5):
    """
    Helper function to restart wifi via ubus and wait for it to settle.

    Args:
        ssh_command: SSH command fixture for executing commands
        timeout: Maximum time to wait for wifi to settle (default: 5 seconds)

    Returns:
        bool: True if wifi restarted successfully, False if timed out
    """
    # Restart wifi
    ssh_command.run("wifi down")
    time.sleep(2)
    ssh_command.run("wifi up")
    time.sleep(2)

    # Wait till network reload finished
    result = ssh_command.run(f"ubus -t {timeout} wait_for hostapd.phy0-ap0")[0]
    return "timed out" not in "\n".join(result)


@pytest.mark.lg_feature("wifi")
def test_wifi_wpa3(ssh_command):
    """
    Test enabling a wifi network with WPA3 encryption.

    This test configures a wifi network with WPA3 encryption and password 'openwrt4all'.
    """
    ssh_command.run("uci delete wireless.radio0.disabled")
    ssh_command.run("uci set wireless.default_radio0.encryption=sae")
    ssh_command.run("uci set wireless.default_radio0.key=openwrt4all")

    ssh_command.run("uci commit")

    restart_wifi_and_wait(ssh_command)

    iwinfo_output = "\n".join(ssh_command.run("iwinfo")[0])

    assert "Mode: Master" in iwinfo_output
    assert "Encryption: WPA3 SAE (CCMP)" in iwinfo_output


@pytest.mark.lg_feature("wifi")
def test_wifi_wpa2(ssh_command):
    """
    Test enabling a wifi network with WPA2 encryption.

    This test configures a wifi network with WPA2 encryption and password 'openwrt4all'.
    """
    ssh_command.run("uci delete wireless.radio0.disabled")
    ssh_command.run("uci set wireless.default_radio0.encryption=psk2")
    ssh_command.run("uci set wireless.default_radio0.key=openwrt4all")

    ssh_command.run("uci commit")

    restart_wifi_and_wait(ssh_command)

    iwinfo_output = "\n".join(ssh_command.run("iwinfo")[0])

    assert "Mode: Master" in iwinfo_output
    assert "Encryption: WPA2 PSK (CCMP)" in iwinfo_output


@pytest.mark.lg_feature("wifi")
@pytest.mark.lg_feature("wifi-neighbors")
def test_wifi_scan(ssh_command):
    """
    Test wifi scanning functionality.

    This test performs a wifi scan and verifies that at least one network is found.
    """
    ssh_command.run("uci delete wireless.radio0.disabled")
    ssh_command.run("uci commit")

    restart_wifi_and_wait(ssh_command)

    # Perform wifi scan
    scan_output = ssh_command.run("iwinfo phy0 scan")
    scan_results = "\n".join(scan_output[0])

    # Check that at least one network was found
    assert "ESSID:" in scan_results


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
    ssh_command.run("uci delete wireless.radio0.disabled")

    ssh_command.run("uci set wireless.default_radio0.encryption=sae-mixed")
    ssh_command.run("uci set wireless.default_radio0.key=testtest")

    ssh_command.run("uci delete wireless.radio1.channel")
    ssh_command.run("uci set wireless.radio1.band=2g")
    ssh_command.run("uci delete wireless.radio1.disabled")

    ssh_command.run("uci set wireless.default_radio1.network=wan")
    ssh_command.run("uci set wireless.default_radio1.mode=sta")
    ssh_command.run("uci set wireless.default_radio1.encryption=sae-mixed")
    ssh_command.run("uci set wireless.default_radio1.key=testtest")

    assert "-wireless.radio1.disabled" in "\n".join(ssh_command.run("uci changes")[0])

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
