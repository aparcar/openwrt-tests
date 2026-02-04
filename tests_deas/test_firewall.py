"""Firewall and security configuration tests for OpenWRT."""

import re
import time

import pytest


class TestFirewall:
    """Tests for firewall functionality and security configurations."""

    def test_firewall_service_status(self, ssh_command):
        """Test that firewall service is running."""
        # Check if firewall is enabled
        enabled = ssh_command.run("uci get firewall.@defaults[0].disable 2>/dev/null")
        if enabled[2] == 0 and enabled[0][0].strip() == "1":
            pytest.skip("Firewall is disabled in configuration")

        # Check firewall service status
        status = ssh_command.run("/etc/init.d/firewall status")
        assert status[2] == 0, "Firewall service is not running"

    def test_firewall_zones(self, ssh_command, results_bag):
        """Test firewall zones configuration."""
        # Get all zones
        zones_output = ssh_command.run_check(
            "uci show firewall | grep 'firewall.@zone'"
        )

        zones = {}
        current_zone = None

        for line in zones_output:
            if "=zone" in line:
                # Extract zone index
                match = re.search(r"firewall\.@zone\[(\d+)\]", line)
                if match:
                    current_zone = f"zone_{match.group(1)}"
                    zones[current_zone] = {}
            elif current_zone and "=" in line:
                # Parse zone properties
                key_match = re.search(r"firewall\.@zone\[\d+\]\.(\w+)=(.+)", line)
                if key_match:
                    key = key_match.group(1)
                    value = key_match.group(2).strip("'\"")
                    zones[current_zone][key] = value

        results_bag["firewall_zones"] = zones

        # Verify at least one zone exists
        assert len(zones) > 0, "No firewall zones configured"

        # Check for common zones
        zone_names = [z.get("name", "") for z in zones.values()]

        # Usually should have at least lan and wan zones
        if "lan" in zone_names:
            lan_zone = next(z for z in zones.values() if z.get("name") == "lan")
            assert lan_zone.get("input", "").upper() == "ACCEPT", (
                "LAN zone should accept input"
            )
            assert lan_zone.get("forward", "").upper() == "ACCEPT", (
                "LAN zone should accept forward"
            )

        if "wan" in zone_names:
            wan_zone = next(z for z in zones.values() if z.get("name") == "wan")
            assert wan_zone.get("input", "").upper() in ["REJECT", "DROP"], (
                "WAN zone should reject/drop input"
            )
            assert wan_zone.get("forward", "").upper() in ["REJECT", "DROP"], (
                "WAN zone should reject/drop forward"
            )

    def test_firewall_rules(self, ssh_command):
        """Test firewall rules are properly loaded."""
        # Check iptables/nftables rules
        # Try nftables first (newer OpenWRT)
        nft_check = ssh_command.run("nft list ruleset 2>/dev/null")

        if nft_check[2] == 0 and nft_check[0]:
            # Using nftables
            rules = nft_check[0]

            # Check for essential chains
            assert "input" in rules.lower(), "No input chain found in nftables"
            assert "forward" in rules.lower(), "No forward chain found in nftables"
            assert "output" in rules.lower(), "No output chain found in nftables"

            # Check for zone rules
            assert (
                "zone" in rules.lower()
                or "lan" in rules.lower()
                or "wan" in rules.lower()
            ), "No zone rules found"
        else:
            # Try iptables
            iptables_check = ssh_command.run("iptables -L -n 2>/dev/null")

            if iptables_check[2] == 0:
                rules = "\n".join(iptables_check)

                # Check for essential chains
                assert "Chain INPUT" in rules, "No INPUT chain found"
                assert "Chain FORWARD" in rules, "No FORWARD chain found"
                assert "Chain OUTPUT" in rules, "No OUTPUT chain found"

                # Check for zone chains
                assert "zone_" in rules.lower() or "reject" in rules.lower(), (
                    "No zone chains found"
                )
            else:
                pytest.skip("Neither nftables nor iptables available")

    def test_firewall_port_forwards(self, ssh_command):
        """Test port forwarding rules."""
        # Get all redirects (port forwards)
        redirects = ssh_command.run("uci show firewall | grep 'firewall.@redirect'")

        if redirects[0]:
            redirect_count = len(
                set(re.findall(r"firewall\.@redirect\[(\d+)\]", "\n".join(redirects)))
            )

            for i in range(redirect_count):
                # Check each redirect has required fields
                name = ssh_command.run(
                    f"uci get firewall.@redirect[{i}].name 2>/dev/null"
                )
                proto = ssh_command.run(
                    f"uci get firewall.@redirect[{i}].proto 2>/dev/null"
                )

                if name[2] == 0:
                    # Redirect exists, verify it has protocol
                    assert proto[2] == 0, f"Redirect {i} missing protocol"

    def test_firewall_custom_rules(self, ssh_command):
        """Test custom firewall rules if configured."""
        # Check for custom rules file
        custom_rules_exist = ssh_command.run("test -f /etc/firewall.user")[2] == 0

        if custom_rules_exist:
            # Check if file is executable
            is_executable = ssh_command.run("test -x /etc/firewall.user")[2] == 0
            assert is_executable, "/etc/firewall.user exists but is not executable"

            # Check syntax (basic)
            syntax_check = ssh_command.run("sh -n /etc/firewall.user")
            assert syntax_check[2] == 0, "Syntax error in /etc/firewall.user"

    def test_firewall_logging(self, ssh_command):
        """Test firewall logging configuration."""
        # Check if logging is enabled
        log_level = ssh_command.run(
            "uci get firewall.@defaults[0].log_level 2>/dev/null"
        )

        if log_level[2] == 0 and log_level[0].strip() != "off":
            # Logging is enabled, check if it's working
            # Look for firewall messages in system log
            ssh_command.run("logread | grep -i firewall | tail -5")

            # We should see some firewall-related messages if logging is active
            # Not asserting as there might legitimately be no recent firewall events

    def test_firewall_defaults(self, ssh_command, results_bag):
        """Test firewall default policies."""
        defaults = {}

        # Get default policies
        for policy in ["input", "output", "forward"]:
            value = ssh_command.run(
                f"uci get firewall.@defaults[0].{policy} 2>/dev/null"
            )
            if value[2] == 0:
                defaults[policy] = value[0].strip().upper()

        results_bag["firewall_defaults"] = defaults

        # Verify secure defaults
        assert defaults.get("input", "ACCEPT") != "ACCEPT", (
            "Default input policy should not be ACCEPT"
        )
        assert defaults.get("forward", "ACCEPT") != "ACCEPT", (
            "Default forward policy should not be ACCEPT"
        )
        # Output can be ACCEPT

    def test_syn_flood_protection(self, ssh_command):
        """Test SYN flood protection settings."""
        syn_flood = ssh_command.run(
            "uci get firewall.@defaults[0].syn_flood 2>/dev/null"
        )

        if syn_flood[2] == 0:
            assert syn_flood[0].strip() == "1", "SYN flood protection should be enabled"

        # Check if synflood_protect is set
        syn_protect = ssh_command.run(
            "uci get firewall.@defaults[0].synflood_protect 2>/dev/null"
        )
        if syn_protect[2] == 0:
            assert syn_protect[0].strip() == "1", (
                "SYN flood protection should be enabled"
            )

    def test_invalid_packets_handling(self, ssh_command):
        """Test handling of invalid packets."""
        # Check if invalid packets are dropped
        drop_invalid = ssh_command.run(
            "uci get firewall.@defaults[0].drop_invalid 2>/dev/null"
        )

        if drop_invalid[2] == 0:
            assert drop_invalid[0].strip() == "1", "Invalid packets should be dropped"

    def test_firewall_zone_forwarding(self, ssh_command):
        """Test zone forwarding rules."""
        # Get all forwarding rules
        forwards = ssh_command.run_check(
            "uci show firewall | grep 'firewall.@forwarding'"
        )

        if forwards[0]:
            # Parse forwarding rules
            forward_rules = []

            forward_count = len(
                set(re.findall(r"firewall\.@forwarding\[(\d+)\]", "\n".join(forwards)))
            )

            for i in range(forward_count):
                src = ssh_command.run(
                    f"uci get firewall.@forwarding[{i}].src 2>/dev/null"
                )
                dest = ssh_command.run(
                    f"uci get firewall.@forwarding[{i}].dest 2>/dev/null"
                )

                if src[2] == 0 and dest[2] == 0:
                    forward_rules.append(
                        {"src": src[0].strip(), "dest": dest[0].strip()}
                    )

            # Common check: LAN should be able to forward to WAN
            lan_to_wan = any(
                r["src"] == "lan" and r["dest"] == "wan" for r in forward_rules
            )
            assert lan_to_wan, "LAN to WAN forwarding should be allowed"

    def test_connection_tracking(self, ssh_command):
        """Test connection tracking settings."""
        # Check conntrack modules
        conntrack_modules = ssh_command.run("lsmod | grep conntrack")
        assert conntrack_modules[0], "No conntrack modules loaded"

        # Check conntrack table size
        conntrack_max = ssh_command.run_check("sysctl net.netfilter.nf_conntrack_max")[
            0
        ]
        max_value = int(conntrack_max.split("=")[1].strip())

        # Should be reasonable for the system
        assert max_value >= 4096, f"Conntrack table too small: {max_value}"

        # Check current connections
        conntrack_count = ssh_command.run(
            "conntrack -C 2>/dev/null || cat /proc/sys/net/netfilter/nf_conntrack_count"
        )
        if conntrack_count[2] == 0 and conntrack_count[0].strip().isdigit():
            current_conns = int(conntrack_count[0].strip())
            assert current_conns < max_value * 0.8, "Conntrack table nearly full"

    def test_firewall_include_files(self, ssh_command):
        """Test firewall include files."""
        # Check for includes
        includes = ssh_command.run("uci show firewall | grep '\\.path='")

        if includes[0]:
            # Verify included files exist and are valid
            for line in includes:
                if ".path=" in line:
                    path_match = re.search(r"\.path='([^']+)'", line)
                    if path_match:
                        include_path = path_match.group(1)
                        exists = ssh_command.run(f"test -f {include_path}")[2] == 0
                        assert exists, (
                            f"Included firewall file {include_path} does not exist"
                        )

    def test_mac_address_filtering(self, ssh_command):
        """Test MAC address filtering if configured."""
        # Check for MAC-based rules
        mac_rules = ssh_command.run(
            "uci show firewall | grep -i 'mac' | grep -v 'macsec'"
        )

        if mac_rules[0]:
            # Verify MAC addresses are in correct format
            mac_pattern = re.compile(r"([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})")

            for line in mac_rules:
                if "=" in line and "mac" in line.lower():
                    value = line.split("=", 1)[1].strip("'\"")
                    if ":" in value or "-" in value:
                        assert mac_pattern.match(value), (
                            f"Invalid MAC address format: {value}"
                        )

    @pytest.mark.slow
    def test_firewall_stress(self, ssh_command):
        """Test firewall under stress conditions."""
        # Create multiple temporary rules
        test_rules = []
        base_port = 50000

        try:
            # Add several test rules
            for i in range(10):
                port = base_port + i
                rule_name = f"test_rule_{i}"

                ssh_command.run_check("uci add firewall rule")
                ssh_command.run_check(f"uci set firewall.@rule[-1].name='{rule_name}'")
                ssh_command.run_check("uci set firewall.@rule[-1].src='wan'")
                ssh_command.run_check(f"uci set firewall.@rule[-1].dest_port='{port}'")
                ssh_command.run_check("uci set firewall.@rule[-1].target='DROP'")
                ssh_command.run_check("uci set firewall.@rule[-1].proto='tcp'")

                test_rules.append(rule_name)

            # Commit and reload
            ssh_command.run_check("uci commit firewall")
            ssh_command.run_check("/etc/init.d/firewall reload")

            # Give firewall time to reload
            time.sleep(2)

            # Verify firewall is still running
            status = ssh_command.run("/etc/init.d/firewall status")
            assert status[2] == 0, "Firewall crashed during stress test"

        finally:
            # Cleanup test rules
            for rule_name in test_rules:
                # Find and delete the rule
                rule_idx = ssh_command.run(
                    f"uci show firewall | grep \"name='{rule_name}'\" | "
                    f"sed -n 's/firewall.@rule\[\([0-9]*\)\].*/\\1/p'"
                )
                if rule_idx[2] == 0 and rule_idx[0].strip():
                    ssh_command.run(f"uci delete firewall.@rule[{rule_idx[0].strip()}]")

            ssh_command.run("uci commit firewall")
            ssh_command.run("/etc/init.d/firewall reload")

    def test_firewall_ipv6(self, ssh_command):
        """Test IPv6 firewall configuration if enabled."""
        # Check if IPv6 is enabled
        ipv6_disable = ssh_command.run(
            "uci get firewall.@defaults[0].disable_ipv6 2>/dev/null"
        )

        if ipv6_disable[2] != 0 or ipv6_disable[0].strip() != "1":
            # IPv6 firewall should be active
            # Check for ip6tables or nft inet tables
            ip6_check = ssh_command.run("ip6tables -L -n 2>/dev/null | head -20")
            nft6_check = ssh_command.run("nft list ruleset 2>/dev/null | grep -i inet")

            if ip6_check[2] == 0 or (nft6_check[2] == 0 and nft6_check[0]):
                # IPv6 firewall is available
                # Check for ICMPv6 rules (essential for IPv6)
                icmpv6_check = ssh_command.run(
                    "ip6tables -L -n 2>/dev/null | grep -i icmpv6 || "
                    "nft list ruleset 2>/dev/null | grep -i icmpv6"
                )
                assert icmpv6_check[0], (
                    "No ICMPv6 rules found - IPv6 may not work correctly"
                )
