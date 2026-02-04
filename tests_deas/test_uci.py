"""UCI (Unified Configuration Interface) tests for OpenWRT."""

import pytest


class TestUCI:
    """Tests for UCI configuration system."""

    def test_uci_basics(self, ssh_command):
        """Test basic UCI commands work correctly."""
        # Test uci show
        output = ssh_command.run_check("uci show system")
        assert output, "UCI show returned no output"
        assert "system." in output[0], "UCI show system failed"

    def test_uci_get_set_delete(self, ssh_command):
        """Test UCI get, set, and delete operations."""
        test_section = "test_uci_temp"
        test_option = "test_option"
        test_value = "test_value_12345"

        try:
            # Clean up any existing test section
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")

            # Create a new section
            ssh_command.run_check(f"uci set {test_section}=config")
            ssh_command.run_check(
                f"uci set {test_section}.{test_option}='{test_value}'"
            )
            ssh_command.run_check("uci commit")

            # Test get
            result = ssh_command.run_check(f"uci get {test_section}.{test_option}")
            assert result[0].strip() == test_value, (
                f"UCI get failed: expected {test_value}, got {result[0]}"
            )

            # Test show
            show_result = ssh_command.run_check(f"uci show {test_section}")
            assert test_value in show_result[0], "UCI show doesn't contain set value"

            # Test delete option
            ssh_command.run_check(f"uci delete {test_section}.{test_option}")
            ssh_command.run_check("uci commit")

            # Verify deletion
            get_result = ssh_command.run(f"uci get {test_section}.{test_option} 2>&1")
            assert get_result[2] != 0, "UCI delete option failed"

        finally:
            # Cleanup
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")

    def test_uci_list_operations(self, ssh_command):
        """Test UCI list operations."""
        test_section = "test_uci_list"
        test_list = "test_list"
        test_values = ["value1", "value2", "value3"]

        try:
            # Clean up
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")

            # Create section with list
            ssh_command.run_check(f"uci set {test_section}=config")

            # Add list values
            for value in test_values:
                ssh_command.run_check(
                    f"uci add_list {test_section}.{test_list}='{value}'"
                )

            ssh_command.run_check("uci commit")

            # Verify list
            result = ssh_command.run_check(f"uci get {test_section}.{test_list}")
            for value in test_values:
                assert value in result[0], f"List missing value: {value}"

            # Test delete from list
            ssh_command.run_check(
                f"uci del_list {test_section}.{test_list}='{test_values[1]}'"
            )
            ssh_command.run_check("uci commit")

            # Verify deletion
            result = ssh_command.run_check(f"uci get {test_section}.{test_list}")
            assert test_values[1] not in result[0], (
                f"Failed to delete {test_values[1]} from list"
            )
            assert test_values[0] in result[0], f"Incorrectly deleted {test_values[0]}"
            assert test_values[2] in result[0], f"Incorrectly deleted {test_values[2]}"

        finally:
            # Cleanup
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")

    def test_uci_revert(self, ssh_command):
        """Test UCI revert functionality."""
        test_section = "test_uci_revert"
        test_option = "test_option"
        original_value = "original"
        new_value = "modified"

        try:
            # Setup
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")

            # Create initial config
            ssh_command.run_check(f"uci set {test_section}=config")
            ssh_command.run_check(
                f"uci set {test_section}.{test_option}='{original_value}'"
            )
            ssh_command.run_check("uci commit")

            # Modify without commit
            ssh_command.run_check(f"uci set {test_section}.{test_option}='{new_value}'")

            # Verify change is pending
            result = ssh_command.run_check(f"uci get {test_section}.{test_option}")
            assert result[0].strip() == new_value, "UCI set didn't update value"

            # Revert changes
            ssh_command.run_check(f"uci revert {test_section}")

            # Verify revert
            result = ssh_command.run_check(f"uci get {test_section}.{test_option}")
            assert result[0].strip() == original_value, (
                f"UCI revert failed: got {result[0]}, expected {original_value}"
            )

        finally:
            # Cleanup
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")

    def test_uci_export_import(self, ssh_command):
        """Test UCI export and import functionality."""
        test_package = "test_export"

        try:
            # Clean up
            ssh_command.run(f"uci delete {test_package} 2>/dev/null")
            ssh_command.run("uci commit")

            # Create test configuration
            ssh_command.run_check(f"uci set {test_package}=config")
            ssh_command.run_check(f"uci set {test_package}.section1=type1")
            ssh_command.run_check(f"uci set {test_package}.section1.option1='value1'")
            ssh_command.run_check(f"uci set {test_package}.section2=type2")
            ssh_command.run_check(f"uci set {test_package}.section2.option2='value2'")
            ssh_command.run_check("uci commit")

            # Export configuration
            export_data = ssh_command.run_check(f"uci export {test_package}")

            # Verify export contains our data
            assert "config type1 'section1'" in "\n".join(export_data)
            assert "option option1 'value1'" in "\n".join(export_data)
            assert "config type2 'section2'" in "\n".join(export_data)
            assert "option option2 'value2'" in "\n".join(export_data)

            # Delete and reimport
            ssh_command.run_check(f"uci delete {test_package}")
            ssh_command.run_check("uci commit")

            # Save export to file and import
            ssh_command.run_check(
                f"echo '{chr(10).join(export_data)}' > /tmp/uci_export_test"
            )
            ssh_command.run_check("uci import < /tmp/uci_export_test")
            ssh_command.run_check("uci commit")

            # Verify import
            result1 = ssh_command.run_check(f"uci get {test_package}.section1.option1")
            assert result1[0].strip() == "value1", "Import failed for section1"

            result2 = ssh_command.run_check(f"uci get {test_package}.section2.option2")
            assert result2[0].strip() == "value2", "Import failed for section2"

        finally:
            # Cleanup
            ssh_command.run(f"uci delete {test_package} 2>/dev/null")
            ssh_command.run("uci commit")
            ssh_command.run("rm -f /tmp/uci_export_test")

    def test_uci_batch_mode(self, ssh_command):
        """Test UCI batch mode operations."""
        test_section = "test_batch"

        try:
            # Create batch commands
            batch_commands = [
                f"set {test_section}=config",
                f"set {test_section}.option1='batch_value1'",
                f"set {test_section}.option2='batch_value2'",
                f"add_list {test_section}.list1='item1'",
                f"add_list {test_section}.list1='item2'",
                "commit",
            ]

            batch_file = "/tmp/uci_batch_test"
            ssh_command.run_check(
                f"echo '{chr(10).join(batch_commands)}' > {batch_file}"
            )

            # Execute batch
            ssh_command.run_check(f"uci batch < {batch_file}")

            # Verify results
            result1 = ssh_command.run_check(f"uci get {test_section}.option1")
            assert result1[0].strip() == "batch_value1", "Batch mode failed for option1"

            result2 = ssh_command.run_check(f"uci get {test_section}.option2")
            assert result2[0].strip() == "batch_value2", "Batch mode failed for option2"

            list_result = ssh_command.run_check(f"uci get {test_section}.list1")
            assert "item1" in list_result[0], "Batch mode failed for list item1"
            assert "item2" in list_result[0], "Batch mode failed for list item2"

        finally:
            # Cleanup
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")
            ssh_command.run(f"rm -f {batch_file}")

    def test_uci_validation(self, ssh_command):
        """Test UCI validation for common configurations."""
        # Test network configuration validation
        network_config = ssh_command.run_check("uci show network")

        # Should have at least loopback interface
        assert "network.loopback" in "\n".join(network_config), (
            "Missing loopback interface"
        )

        # Check if lan interface exists and has valid protocol
        lan_proto = ssh_command.run("uci get network.lan.proto 2>/dev/null")
        if lan_proto[2] == 0:
            valid_protos = ["static", "dhcp", "none"]
            assert lan_proto[0].strip() in valid_protos, (
                f"Invalid LAN protocol: {lan_proto[0]}"
            )

    def test_uci_changes_tracking(self, ssh_command):
        """Test UCI changes tracking functionality."""
        test_section = "test_changes"

        try:
            # Ensure clean state
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")

            # Make some changes without committing
            ssh_command.run_check(f"uci set {test_section}=config")
            ssh_command.run_check(f"uci set {test_section}.option1='value1'")
            ssh_command.run_check(f"uci set {test_section}.option2='value2'")

            # Check changes
            changes = ssh_command.run_check("uci changes")
            changes_text = "\n".join(changes)

            assert f"{test_section}" in changes_text, "Changes not tracked"
            assert "option1" in changes_text, "Option1 change not tracked"
            assert "option2" in changes_text, "Option2 change not tracked"

            # Commit and verify no pending changes
            ssh_command.run_check("uci commit")
            changes_after = ssh_command.run_check("uci changes")
            assert not changes_after[0].strip(), "Changes remain after commit"

        finally:
            # Cleanup
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")

    def test_uci_system_config(self, ssh_command, results_bag):
        """Test and validate system UCI configuration."""
        # Get hostname
        hostname = ssh_command.run_check("uci get system.@system[0].hostname")[
            0
        ].strip()
        results_bag["hostname"] = hostname

        # Get timezone
        timezone = ssh_command.run("uci get system.@system[0].timezone")[0].strip()
        if timezone:
            results_bag["timezone"] = timezone

        # Verify system has at least basic configuration
        assert hostname, "No hostname configured"
        assert hostname != "OpenWrt", "Using default hostname"

    def test_uci_config_permissions(self, ssh_command):
        """Test UCI configuration file permissions."""
        # Check /etc/config directory permissions
        config_dir_perms = ssh_command.run_check("stat -c '%a' /etc/config")[0].strip()
        assert config_dir_perms == "755", (
            f"Incorrect /etc/config permissions: {config_dir_perms}"
        )

        # Check permissions of key config files
        important_configs = ["system", "network", "wireless", "firewall", "dhcp"]

        for config in important_configs:
            if ssh_command.run(f"test -f /etc/config/{config}")[2] == 0:
                perms = ssh_command.run_check(f"stat -c '%a' /etc/config/{config}")[
                    0
                ].strip()
                assert perms in ["644", "600"], (
                    f"Incorrect permissions for /etc/config/{config}: {perms}"
                )

    @pytest.mark.slow
    def test_uci_stress(self, ssh_command):
        """Stress test UCI with multiple operations."""
        test_section = "test_stress"
        num_options = 50

        try:
            # Clean state
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")

            # Create many options
            ssh_command.run_check(f"uci set {test_section}=config")

            for i in range(num_options):
                ssh_command.run_check(f"uci set {test_section}.option{i}='value{i}'")

            # Commit all at once
            ssh_command.run_check("uci commit")

            # Verify random samples
            import random

            for _ in range(10):
                i = random.randint(0, num_options - 1)
                result = ssh_command.run_check(f"uci get {test_section}.option{i}")
                assert result[0].strip() == f"value{i}", (
                    f"Stress test failed for option{i}"
                )

            # Test deletion of all
            ssh_command.run_check(f"uci delete {test_section}")
            ssh_command.run_check("uci commit")

            # Verify deletion
            result = ssh_command.run(f"uci show {test_section} 2>&1")
            assert result[2] != 0, "Failed to delete stress test section"

        finally:
            # Ensure cleanup
            ssh_command.run(f"uci delete {test_section} 2>/dev/null")
            ssh_command.run("uci commit")
