"""Tests for device discovery manager."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from labgrid_runner.device_discovery import (
    DeviceDiscoveryManager,
    DiscoveredDevice,
)
from labgrid_runner.labgrid_client import Place


@pytest.fixture
def sample_places():
    """Sample places for testing."""
    return {
        "testlab-openwrt_one": Place(
            name="testlab-openwrt_one",
            acquired=False,
            tags={"device_type": "openwrt_one", "features": "wifi,wan_port"},
        ),
        "testlab-openwrt_one-2": Place(
            name="testlab-openwrt_one-2",
            acquired=False,
            tags={"device_type": "openwrt_one", "features": "wifi"},
        ),
        "testlab-archer-c7": Place(
            name="testlab-archer-c7",
            acquired=False,
            tags={"device_type": "tplink_archer-c7-v2"},
        ),
        "testlab-bananapi_bpi-r4": Place(
            name="testlab-bananapi_bpi-r4",
            acquired=True,
            acquired_by="user@host",
            tags={"device_type": "bananapi_bpi-r4", "features": "wan_port"},
        ),
    }


@pytest.fixture
def mock_labgrid_client(sample_places):
    """Create mock labgrid client with sample places."""
    client = MagicMock()
    client.get_places = AsyncMock(return_value=sample_places)
    return client


class TestDiscoveredDevice:
    """Tests for DiscoveredDevice dataclass."""

    def test_instance_count(self):
        """Test instance count property."""
        device = DiscoveredDevice(
            device_type="openwrt_one",
            places=[
                Place(name="lab-openwrt_one", acquired=False),
                Place(name="lab-openwrt_one-2", acquired=False),
            ],
        )
        assert device.instance_count == 2

    def test_empty_places(self):
        """Test device with no places."""
        device = DiscoveredDevice(device_type="test")
        assert device.instance_count == 0
        assert device.features == set()
        assert device.has_target_file is False


class TestDeviceDiscoveryManager:
    """Tests for DeviceDiscoveryManager."""

    @pytest.mark.asyncio
    async def test_discover_devices(self, mock_labgrid_client):
        """Test basic device discovery."""
        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
        )

        devices = await manager.discover()

        assert len(devices) == 3
        assert "openwrt_one" in devices
        assert "tplink_archer-c7-v2" in devices
        assert "bananapi_bpi-r4" in devices

    @pytest.mark.asyncio
    async def test_discover_counts_instances(self, mock_labgrid_client):
        """Test that instance count is correct."""
        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
        )

        devices = await manager.discover()

        # openwrt_one has 2 instances
        assert devices["openwrt_one"].instance_count == 2
        # archer-c7 has 1 instance
        assert devices["tplink_archer-c7-v2"].instance_count == 1
        # bananapi has 1 instance
        assert devices["bananapi_bpi-r4"].instance_count == 1

    @pytest.mark.asyncio
    async def test_discover_extracts_features(self, mock_labgrid_client):
        """Test feature extraction from place tags."""
        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
        )

        devices = await manager.discover()

        # openwrt_one has wifi and wan_port from tags (merged from both instances)
        assert "wifi" in devices["openwrt_one"].features
        assert "wan_port" in devices["openwrt_one"].features

        # bananapi has wan_port
        assert "wan_port" in devices["bananapi_bpi-r4"].features

        # archer-c7 has no features in tags
        assert len(devices["tplink_archer-c7-v2"].features) == 0

    @pytest.mark.asyncio
    async def test_discover_validates_target_files(self, mock_labgrid_client, tmp_path):
        """Test target file validation."""
        # Create a target file for one device
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "openwrt_one.yaml").write_text(
            "targets:\n  main:\n    features:\n      - rootfs\n"
        )

        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
            targets_dir=targets_dir,
        )

        devices = await manager.discover()

        assert devices["openwrt_one"].has_target_file is True
        assert devices["tplink_archer-c7-v2"].has_target_file is False
        assert devices["bananapi_bpi-r4"].has_target_file is False

    @pytest.mark.asyncio
    async def test_discover_merges_features_from_file(
        self, mock_labgrid_client, tmp_path
    ):
        """Test that features from target file are merged with tag features."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "openwrt_one.yaml").write_text(
            "targets:\n  main:\n    features:\n      - rootfs\n      - hwsim\n"
        )

        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
            targets_dir=targets_dir,
        )

        devices = await manager.discover()

        # Should have features from both tags and file
        assert "wifi" in devices["openwrt_one"].features  # from tags
        assert "wan_port" in devices["openwrt_one"].features  # from tags
        assert "rootfs" in devices["openwrt_one"].features  # from file
        assert "hwsim" in devices["openwrt_one"].features  # from file

    @pytest.mark.asyncio
    async def test_require_target_files_filters(self, mock_labgrid_client, tmp_path):
        """Test that require_target_files filters devices."""
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir()
        (targets_dir / "openwrt_one.yaml").write_text("targets: {}")

        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
            targets_dir=targets_dir,
            require_target_files=True,
        )

        devices = await manager.discover()

        # Only openwrt_one has a target file
        assert "openwrt_one" in devices
        assert "tplink_archer-c7-v2" not in devices
        assert "bananapi_bpi-r4" not in devices

    @pytest.mark.asyncio
    async def test_caching(self, mock_labgrid_client):
        """Test that discovery results are cached."""
        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
            refresh_interval=300,
        )

        # First call
        await manager.discover()
        assert mock_labgrid_client.get_places.call_count == 1

        # Second call (should use cache)
        await manager.discover()
        assert mock_labgrid_client.get_places.call_count == 1

        # Force refresh
        await manager.discover(force_refresh=True)
        assert mock_labgrid_client.get_places.call_count == 2

    @pytest.mark.asyncio
    async def test_get_device_types(self, mock_labgrid_client):
        """Test get_device_types method."""
        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
        )

        await manager.discover()
        device_types = manager.get_device_types()

        assert set(device_types) == {
            "openwrt_one",
            "tplink_archer-c7-v2",
            "bananapi_bpi-r4",
        }

    @pytest.mark.asyncio
    async def test_get_all_features(self, mock_labgrid_client):
        """Test get_all_features method."""
        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
        )

        await manager.discover()
        features = manager.get_all_features()

        assert "wifi" in features
        assert "wan_port" in features

    @pytest.mark.asyncio
    async def test_get_device(self, mock_labgrid_client):
        """Test get_device method."""
        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
        )

        await manager.discover()

        device = manager.get_device("openwrt_one")
        assert device is not None
        assert device.device_type == "openwrt_one"

        # Non-existent device
        assert manager.get_device("nonexistent") is None

    @pytest.mark.asyncio
    async def test_has_device(self, mock_labgrid_client):
        """Test has_device method."""
        manager = DeviceDiscoveryManager(
            labgrid_client=mock_labgrid_client,
        )

        await manager.discover()

        assert manager.has_device("openwrt_one") is True
        assert manager.has_device("nonexistent") is False

    @pytest.mark.asyncio
    async def test_skips_places_without_device_type(self):
        """Test that places without device_type are skipped."""
        client = MagicMock()
        # Use a name that doesn't follow the lab-device naming convention
        # so it won't have a device_type from either tags or name parsing
        client.get_places = AsyncMock(
            return_value={
                "nodevicetype": Place(name="nodevicetype", acquired=False, tags={}),
                "testlab-openwrt_one": Place(
                    name="testlab-openwrt_one",
                    acquired=False,
                    tags={"device_type": "openwrt_one"},
                ),
            }
        )

        manager = DeviceDiscoveryManager(
            labgrid_client=client,
        )

        devices = await manager.discover()

        # Should only have the device with device_type (from tags)
        # The "nodevicetype" place has no tags and name doesn't match pattern
        assert len(devices) == 1
        assert "openwrt_one" in devices


class TestFeatureExtraction:
    """Tests for feature extraction from target config."""

    def test_extract_features_from_explicit_list(self):
        """Test extracting features from explicit features list."""
        manager = DeviceDiscoveryManager(
            labgrid_client=MagicMock(),
        )

        config = {"features": ["wifi", "wan_port", "usb"]}
        features = manager._extract_features_from_config(config)

        assert features == {"wifi", "wan_port", "usb"}

    def test_extract_features_from_target_section(self):
        """Test extracting features from targets section."""
        manager = DeviceDiscoveryManager(
            labgrid_client=MagicMock(),
        )

        config = {
            "targets": {
                "main": {
                    "features": ["rootfs", "hwsim"],
                }
            }
        }
        features = manager._extract_features_from_config(config)

        assert features == {"rootfs", "hwsim"}

    def test_extract_features_inferred_from_resources(self):
        """Test inferring features from resources."""
        manager = DeviceDiscoveryManager(
            labgrid_client=MagicMock(),
        )

        config = {
            "targets": {
                "main": {
                    "resources": [
                        {"NetworkService": {"address": "192.168.1.1"}},
                        {"USBSerialPort": {"match": {}}},
                    ],
                    "drivers": [
                        {"QEMUDriver": {"memory": "256M"}},
                    ],
                }
            }
        }
        features = manager._extract_features_from_config(config)

        assert "wifi" in features  # From NetworkService
        assert "usb" in features  # From USBSerialPort
        assert "hwsim" in features  # From QEMUDriver

    def test_extract_features_empty_config(self):
        """Test extracting features from empty config."""
        manager = DeviceDiscoveryManager(
            labgrid_client=MagicMock(),
        )

        assert manager._extract_features_from_config({}) == set()
        assert manager._extract_features_from_config(None) == set()
