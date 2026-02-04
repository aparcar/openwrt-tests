"""
Device Discovery Manager

Discovers devices from the labgrid coordinator and provides
a unified interface for device/feature enumeration.

Replaces static target file scanning with dynamic coordinator-based
discovery filtered by lab name.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

import yaml

from .labgrid_client import LabgridClient, Place

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredDevice:
    """Represents a discovered device type with its metadata."""

    device_type: str
    places: list[Place] = field(default_factory=list)
    features: set[str] = field(default_factory=set)
    has_target_file: bool = False

    @property
    def instance_count(self) -> int:
        """Number of physical instances of this device type."""
        return len(self.places)


class DeviceDiscoveryManager:
    """
    Manages dynamic device discovery from labgrid coordinator.

    Replaces static target file scanning with coordinator-based
    discovery filtered by lab name.

    Place naming convention:
        {lab_name}-{device_type}[-{instance}]

    Examples:
        - aparcar-openwrt_one
        - aparcar-openwrt_one-2
        - hsn-bananapi_bpi-r4

    Alternative: Use explicit tags on places:
        labgrid-client set-tags aparcar-openwrt_one \\
            device_type=openwrt_one lab=aparcar features=wifi,wan_port
    """

    def __init__(
        self,
        labgrid_client: LabgridClient,
        targets_dir: Path | None = None,
        refresh_interval: float = 300.0,
        require_target_files: bool = False,
    ):
        """
        Initialize the discovery manager.

        Args:
            labgrid_client: Client for coordinator queries
            targets_dir: Directory containing target YAML files (optional)
            refresh_interval: Seconds between automatic cache refreshes
            require_target_files: If True, filter out devices without target files
        """
        self._client = labgrid_client
        self.targets_dir = targets_dir
        self.refresh_interval = refresh_interval
        self.require_target_files = require_target_files

        # Discovered state
        self._devices: dict[str, DiscoveredDevice] = {}
        self._last_refresh: float = 0
        self._lock = asyncio.Lock()

    async def discover(
        self, force_refresh: bool = False
    ) -> dict[str, DiscoveredDevice]:
        """
        Discover devices from coordinator.

        Args:
            force_refresh: Force refresh even if cache is valid

        Returns:
            Dict mapping device_type to DiscoveredDevice
        """
        async with self._lock:
            now = monotonic()

            # Use cached if still valid
            if (
                not force_refresh
                and self._devices
                and (now - self._last_refresh) < self.refresh_interval
            ):
                return self._devices

            logger.info("Discovering devices from coordinator")

            # Get all places from coordinator (each lab has its own coordinator)
            all_places = await self._client.get_places(refresh=True)
            places = list(all_places.values())

            logger.info(f"Found {len(places)} places on coordinator")

            # Group by device type
            devices: dict[str, DiscoveredDevice] = {}

            for place in places:
                device_type = place.device_type
                if not device_type:
                    logger.warning(
                        f"Place {place.name} has no device_type, skipping"
                    )
                    continue

                if device_type not in devices:
                    devices[device_type] = DiscoveredDevice(device_type=device_type)

                devices[device_type].places.append(place)

                # Extract features from place tags
                if place.tags and "features" in place.tags:
                    features = place.tags["features"].split(",")
                    devices[device_type].features.update(
                        f.strip() for f in features if f.strip()
                    )

            # Validate target files and extract additional features
            if self.targets_dir:
                self._validate_and_enrich(devices)

            # Filter out devices without target files if required
            if self.require_target_files:
                devices = {
                    dt: dev
                    for dt, dev in devices.items()
                    if dev.has_target_file
                }

            self._devices = devices
            self._last_refresh = now

            logger.info(
                f"Discovered {len(devices)} device types: {list(devices.keys())}"
            )

            return devices

    def _validate_and_enrich(
        self, devices: dict[str, DiscoveredDevice]
    ) -> None:
        """
        Validate target files exist and extract features.

        Args:
            devices: Dict of discovered devices to validate/enrich
        """
        for device_type, device in devices.items():
            target_file = self.targets_dir / f"{device_type}.yaml"

            if target_file.exists():
                device.has_target_file = True

                # Extract features from target file
                try:
                    with open(target_file) as f:
                        config = yaml.safe_load(f)

                    file_features = self._extract_features_from_config(config)
                    device.features.update(file_features)

                except Exception as e:
                    logger.warning(f"Error reading target file {target_file}: {e}")
            else:
                logger.warning(
                    f"No target file for device type {device_type}: {target_file}"
                )

    def _extract_features_from_config(self, config: dict) -> set[str]:
        """Extract features from labgrid target configuration."""
        features = set()

        if not config:
            return features

        # Check for explicit features at top level
        if "features" in config:
            features.update(config["features"])
            return features

        # Check targets section
        targets = config.get("targets", {})
        for target_name, target_config in targets.items():
            if not isinstance(target_config, dict):
                continue

            # Check for features in target
            if "features" in target_config:
                features.update(target_config["features"])
                continue

            # Infer from resources/drivers
            resources = target_config.get("resources", [])
            drivers = target_config.get("drivers", [])

            # Handle resources as list of dicts
            resource_names = set()
            if isinstance(resources, list):
                for r in resources:
                    if isinstance(r, dict):
                        resource_names.update(r.keys())
            elif isinstance(resources, dict):
                resource_names.update(resources.keys())

            # Handle drivers as list of dicts
            driver_names = set()
            if isinstance(drivers, list):
                for d in drivers:
                    if isinstance(d, dict):
                        driver_names.update(d.keys())
            elif isinstance(drivers, dict):
                driver_names.update(drivers.keys())

            if "NetworkService" in resource_names or "WifiAP" in resource_names:
                features.add("wifi")
            if "EthernetInterface" in resource_names:
                features.add("wan_port")
            if any("USB" in r for r in resource_names):
                features.add("usb")
            if "QEMUDriver" in driver_names:
                features.add("hwsim")

        return features

    def get_device_types(self) -> list[str]:
        """Get list of discovered device types."""
        return list(self._devices.keys())

    def get_all_features(self) -> list[str]:
        """Get aggregated list of all features across devices."""
        all_features = set()
        for device in self._devices.values():
            all_features.update(device.features)
        return list(all_features)

    def get_device(self, device_type: str) -> DiscoveredDevice | None:
        """Get discovered device by type."""
        return self._devices.get(device_type)

    def has_device(self, device_type: str) -> bool:
        """Check if device type is discovered."""
        return device_type in self._devices
