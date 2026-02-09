"""
Test Types and Image Profiles

Defines different test types (firmware tests vs kernel selftests) and
the image profiles needed for each. This allows the scheduler to create
appropriate jobs with the right firmware images.

Test Types:
- firmware: Standard OpenWrt functionality tests (boot, network, wifi, packages)
- kselftest: Linux kernel validation tests (net, timers, seccomp, bpf)

Each test type may require:
- Different packages in the firmware image
- Different device capabilities (cabling, isolation)
- Different test repositories
"""

from dataclasses import dataclass, field
from enum import Enum


class TestType(str, Enum):
    """Types of tests that can be run on devices."""

    FIRMWARE = "firmware"  # OpenWrt functionality tests
    KSELFTEST = "kselftest"  # Linux kernel selftests


@dataclass
class ImageProfile:
    """
    Defines packages and configuration for a firmware image.

    Used to request custom builds from ASU when additional
    packages are needed for specific test types.
    """

    name: str
    description: str
    packages: list[str] = field(default_factory=list)
    # If True, use standard image without custom build
    use_standard_image: bool = False


@dataclass
class TestTypeConfig:
    """
    Configuration for a test type.

    Defines what image profile to use, which test plans are available,
    and what device capabilities are required.
    """

    test_type: TestType
    description: str
    image_profile: str
    test_plans: list[str]
    # Device must have ALL of these capabilities
    required_capabilities: list[str] = field(default_factory=list)
    # Test repository configuration (can override defaults)
    tests_repo: str | None = None
    tests_branch: str | None = None
    tests_subdir: str | None = None


# =============================================================================
# Image Profiles
# =============================================================================

IMAGE_PROFILES: dict[str, ImageProfile] = {
    "standard": ImageProfile(
        name="standard",
        description="Default OpenWrt image without additional packages",
        packages=[],
        use_standard_image=True,
    ),
    "kselftest": ImageProfile(
        name="kselftest",
        description="Image with kselftest dependencies",
        packages=[
            # Shell and scripting
            "bash",
            "python3",
            "python3-base",
            # Networking tools for net tests
            "iproute2-full",
            "ethtool",
            "iperf3",
            "iputils-ping",
            # Process utilities
            "procps-ng",
            "coreutils",
            # Kselftest packages
            "kselftests-net",
            "kselftests-timers",
            "kselftests-size",
            "kselftests-rtc",
            "kselftests-futex",
            "kselftests-exec",
            "kselftests-clone3",
            "kselftests-openat2",
            "kselftests-mincore",
            "kselftests-mqueue",
            "kselftests-kcmp",
            "kselftests-sigaltstack",
            "kselftests-splice",
            "kselftests-sync",
        ],
        use_standard_image=False,
    ),
    "kselftest-minimal": ImageProfile(
        name="kselftest-minimal",
        description="Minimal kselftest image for constrained devices",
        packages=[
            "bash",
            "iproute2-full",
            "kselftests-net",
            "kselftests-timers",
            "kselftests-size",
        ],
        use_standard_image=False,
    ),
}


# =============================================================================
# Test Type Configurations
# =============================================================================

TEST_TYPE_CONFIGS: dict[TestType, TestTypeConfig] = {
    TestType.FIRMWARE: TestTypeConfig(
        test_type=TestType.FIRMWARE,
        description="OpenWrt functionality tests",
        image_profile="standard",
        test_plans=["firmware"],
        required_capabilities=[],
    ),
    TestType.KSELFTEST: TestTypeConfig(
        test_type=TestType.KSELFTEST,
        description="Linux kernel selftests",
        image_profile="kselftest",
        test_plans=["kselftest"],
        required_capabilities=[
            "serial_console",
            "isolated_network",
        ],
        tests_subdir="kselftest",
    ),
}


# =============================================================================
# Device Capabilities
# =============================================================================

# Known device capabilities that can be declared by labs
DEVICE_CAPABILITIES = {
    # Basic connectivity
    "serial_console": "Device has serial console access",
    "wan_port": "Device has WAN port connected to internet",
    "lan_ports": "Device has LAN ports for local testing",
    "wifi": "Device has WiFi capability",
    # Testing infrastructure
    "isolated_network": "Device is on isolated network for kernel tests",
    "loopback_ethernet": "Device has Ethernet loopback for net tests",
    "power_control": "Device power can be controlled (PDU)",
    # Performance
    "high_memory": "Device has >= 512MB RAM",
    "external_storage": "Device has external storage (USB/SD)",
}


# =============================================================================
# Helper Functions
# =============================================================================


def get_image_profile(profile_name: str) -> ImageProfile | None:
    """Get an image profile by name."""
    return IMAGE_PROFILES.get(profile_name)


def get_test_type_config(test_type: TestType) -> TestTypeConfig | None:
    """Get configuration for a test type."""
    return TEST_TYPE_CONFIGS.get(test_type)


def get_packages_for_test_type(test_type: TestType) -> list[str]:
    """Get the packages needed for a test type."""
    config = get_test_type_config(test_type)
    if not config:
        return []

    profile = get_image_profile(config.image_profile)
    if not profile:
        return []

    return profile.packages


def device_supports_test_type(
    device_capabilities: list[str], test_type: TestType
) -> bool:
    """Check if a device with given capabilities can run a test type."""
    config = get_test_type_config(test_type)
    if not config:
        return False

    # Device must have all required capabilities
    for required in config.required_capabilities:
        if required not in device_capabilities:
            return False

    return True


def needs_custom_image(test_type: TestType) -> bool:
    """Check if a test type needs a custom-built image."""
    config = get_test_type_config(test_type)
    if not config:
        return False

    profile = get_image_profile(config.image_profile)
    if not profile:
        return False

    return not profile.use_standard_image
