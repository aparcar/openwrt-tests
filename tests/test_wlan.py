import time
import pexpect
import sys

# Test wifi configuration.
# This test works best with mac0211 hwsim driver, no other was tested
# This creates one AP and one station and checks if they an connect to each other.
def test_wifi_sae_mixed(ssh_command):
    ssh_command.run("uci set wireless.radio0.channel=11")
    ssh_command.run("uci set wireless.radio0.band=2g")
    ssh_command.run("uci delete wireless.radio0.disabled")
    # mac80211 hwsim does not support some features, deactivate them
    ssh_command.run("uci set wireless.radio0.ldpc=0")
    ssh_command.run("uci set wireless.radio0.rx_stbc=0")
    ssh_command.run("uci set wireless.radio0.max_amsdu=0")

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
    assert not "timed out" in "\n".join(ssh_command.run("ubus -t 5 wait_for hostapd.phy0-ap0")[0])

    assert "Mode: Master  Channel: 11 (2.462 GHz)" in "\n".join(ssh_command.run("iwinfo")[0])

    # Wait till the client associated
    time.sleep(20)

    assert "Mode: Client  Channel: 11 (2.462 GHz)" in "\n".join(ssh_command.run("iwinfo")[0])

    assert "expected throughput" in "\n".join(ssh_command.run("iwinfo phy0-ap0 assoclist")[0])
    assert "expected throughput" in "\n".join(ssh_command.run("iwinfo phy1-sta0 assoclist")[0])

    ssh_command.run("uci set wireless.default_radio1.encryption=psk2")
    assert "wireless.default_radio1.encryption='psk2'" in "\n".join(ssh_command.run("uci changes")[0])
    ssh_command.run("uci commit")
    ssh_command.run("service network reload")

    # Wait till the wifi client is removed
    time.sleep(1)

    # wait till network reload finished
    assert not "timed out" in "\n".join(ssh_command.run("ubus -t 5 wait_for wpa_supplicant.phy1-sta0")[0])

    assert not "expected throughput" in "\n".join(ssh_command.run("iwinfo phy0-ap0 assoclist")[0])

    # Wait till the client associated
    time.sleep(20)

    assert "expected throughput" in "\n".join(ssh_command.run("iwinfo phy0-ap0 assoclist")[0])

