import os

import pytest


@pytest.mark.skipif(
    os.getenv("LG_FEATURE_APK") is None, reason="LG_FEATURE_APK not defined"
)
class TestApk:
    def test_apk_procd_installed(self, ssh_command):
        assert "procd" in "\n".join(ssh_command.run_check("apk list"))

    @pytest.mark.skipif(
        os.getenv("LG_TEST_ONLINE") is None, reason="LG_TEST_ONLINE not defined"
    )
    def test_apk_add_ucert(self, ssh_command):
        try:
            ssh_command.run_check("apk add ucert")
            assert "ucert" in "\n".join(ssh_command.run_check("apk list | grep ucert"))
        finally:
            ssh_command.run("apk del ucert")

    def test_apk_audit(self, ssh_command, check):
        changes = ssh_command.run_check("apk audit")
        expected_changes = [
            "A etc/urandom.seed",
            "A etc/board.json",
            "A etc/apk/arch",
            "A etc/apk/repositories.d/customfeeds.list",
            "A etc/apk/repositories.d/distfeeds.list",
            "A etc/apk/world",
            "U etc/config/dhcp",
            "A etc/config/network",
            "A etc/config/system",
            "A etc/dropbear/dropbear_rsa_host_key",
            "A etc/dropbear/dropbear_ed25519_host_key",
            "U etc/group",
            "U etc/passwd",
            "A etc/rc.d/K10gpio_switch",
            "A etc/rc.d/K50dropbear",
            "A etc/rc.d/K85odhcpd",
            "A etc/rc.d/K89log",
            "A etc/rc.d/K90boot",
            "A etc/rc.d/K90network",
            "A etc/rc.d/K90sysfixtime",
            "A etc/rc.d/K90umount",
            "A etc/rc.d/S00sysfixtime",
            "A etc/rc.d/S00urngd",
            "A etc/rc.d/S10boot",
            "A etc/rc.d/S10system",
            "A etc/rc.d/S11sysctl",
            "A etc/rc.d/S12log",
            "A etc/rc.d/S19dnsmasq",
            "A etc/rc.d/S19dropbear",
            "A etc/rc.d/S19firewall",
            "A etc/rc.d/S20network",
            "A etc/rc.d/S25packet_steering",
            "A etc/rc.d/S35odhcpd",
            "A etc/rc.d/S50cron",
            "A etc/rc.d/S94gpio_switch",
            "A etc/rc.d/S95done",
            "A etc/rc.d/S96led",
            "A etc/rc.d/S98sysntpd",
            "A etc/rc.d/S99urandom_seed",
            "U etc/shadow",
        ]

        for expected in expected_changes:
            with check:
                assert expected in changes, (
                    f"Expected change '{expected}' not found in audit output"
                )
