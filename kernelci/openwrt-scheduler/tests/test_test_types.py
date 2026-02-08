"""Tests for test types and image profiles."""

import pytest

from openwrt_scheduler.test_types import (
    IMAGE_PROFILES,
    TEST_TYPE_CONFIGS,
    TestType,
    device_supports_test_type,
    get_image_profile,
    get_packages_for_test_type,
    get_test_type_config,
    needs_custom_image,
)


class TestTestType:
    """Tests for TestType enum."""

    def test_firmware_type(self):
        """Test firmware test type."""
        assert TestType.FIRMWARE.value == "firmware"

    def test_kselftest_type(self):
        """Test kselftest type."""
        assert TestType.KSELFTEST.value == "kselftest"

    def test_from_string(self):
        """Test creating from string value."""
        assert TestType("firmware") == TestType.FIRMWARE
        assert TestType("kselftest") == TestType.KSELFTEST

    def test_invalid_type_raises(self):
        """Test that invalid type raises ValueError."""
        with pytest.raises(ValueError):
            TestType("invalid")


class TestImageProfiles:
    """Tests for image profiles."""

    def test_standard_profile_exists(self):
        """Test that standard profile exists."""
        assert "standard" in IMAGE_PROFILES

    def test_kselftest_profile_exists(self):
        """Test that kselftest profile exists."""
        assert "kselftest" in IMAGE_PROFILES

    def test_standard_uses_standard_image(self):
        """Test that standard profile uses standard image."""
        profile = IMAGE_PROFILES["standard"]
        assert profile.use_standard_image is True
        assert profile.packages == []

    def test_kselftest_has_packages(self):
        """Test that kselftest profile has required packages."""
        profile = IMAGE_PROFILES["kselftest"]
        assert profile.use_standard_image is False
        assert "bash" in profile.packages
        assert "python3" in profile.packages

    def test_get_image_profile(self):
        """Test getting image profile by name."""
        profile = get_image_profile("standard")
        assert profile is not None
        assert profile.name == "standard"

        profile = get_image_profile("nonexistent")
        assert profile is None


class TestTestTypeConfigs:
    """Tests for test type configurations."""

    def test_firmware_config_exists(self):
        """Test that firmware config exists."""
        assert TestType.FIRMWARE in TEST_TYPE_CONFIGS

    def test_kselftest_config_exists(self):
        """Test that kselftest config exists."""
        assert TestType.KSELFTEST in TEST_TYPE_CONFIGS

    def test_firmware_config(self):
        """Test firmware configuration."""
        config = TEST_TYPE_CONFIGS[TestType.FIRMWARE]
        assert config.image_profile == "standard"
        assert "boot" in config.test_plans
        assert "serial_console" in config.required_capabilities

    def test_kselftest_config(self):
        """Test kselftest configuration."""
        config = TEST_TYPE_CONFIGS[TestType.KSELFTEST]
        assert config.image_profile == "kselftest"
        assert "kselftest_net" in config.test_plans
        assert "isolated_network" in config.required_capabilities
        assert config.tests_subdir == "kselftest"

    def test_get_test_type_config(self):
        """Test getting config by test type."""
        config = get_test_type_config(TestType.FIRMWARE)
        assert config is not None
        assert config.test_type == TestType.FIRMWARE


class TestDeviceSupportsTestType:
    """Tests for device capability checking."""

    def test_firmware_with_serial_console(self):
        """Test that device with serial_console supports firmware tests."""
        capabilities = ["serial_console", "wan_port"]
        assert device_supports_test_type(capabilities, TestType.FIRMWARE) is True

    def test_firmware_without_serial_console(self):
        """Test that device without serial_console doesn't support firmware."""
        capabilities = ["wan_port"]
        assert device_supports_test_type(capabilities, TestType.FIRMWARE) is False

    def test_kselftest_with_required_capabilities(self):
        """Test kselftest with all required capabilities."""
        capabilities = ["serial_console", "isolated_network", "high_memory"]
        assert device_supports_test_type(capabilities, TestType.KSELFTEST) is True

    def test_kselftest_missing_isolated_network(self):
        """Test kselftest without isolated_network."""
        capabilities = ["serial_console", "wan_port"]
        assert device_supports_test_type(capabilities, TestType.KSELFTEST) is False


class TestNeedsCustomImage:
    """Tests for custom image detection."""

    def test_firmware_no_custom_image(self):
        """Test that firmware tests don't need custom image."""
        assert needs_custom_image(TestType.FIRMWARE) is False

    def test_kselftest_needs_custom_image(self):
        """Test that kselftest needs custom image."""
        assert needs_custom_image(TestType.KSELFTEST) is True


class TestGetPackagesForTestType:
    """Tests for getting packages for test type."""

    def test_firmware_no_packages(self):
        """Test that firmware tests need no extra packages."""
        packages = get_packages_for_test_type(TestType.FIRMWARE)
        assert packages == []

    def test_kselftest_has_packages(self):
        """Test that kselftest has required packages."""
        packages = get_packages_for_test_type(TestType.KSELFTEST)
        assert "bash" in packages
        assert "python3" in packages
