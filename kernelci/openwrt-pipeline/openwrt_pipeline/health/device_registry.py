"""
Device Registry for Health Tracking

Maintains the health status of all devices across labs:
- Healthy: Device is working correctly
- Failing: Device has recent failures but not yet disabled
- Disabled: Device disabled due to persistent failures
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from ..api_client import KernelCIClient
from ..models import Device, DeviceStatus

logger = logging.getLogger(__name__)


class DeviceRegistry:
    """
    Registry for tracking device health status.

    Stores device state including:
    - Current health status
    - Last check time
    - Consecutive failure count
    - Associated GitHub issue (if any)
    """

    def __init__(self, api_client: KernelCIClient, config: dict):
        """
        Initialize the device registry.

        Args:
            api_client: KernelCI API client
            config: Health check configuration
        """
        self.api_client = api_client
        self.config = config

        # Thresholds
        self.warning_threshold = config.get("warning_threshold", 3)
        self.disable_threshold = config.get("disable_threshold", 5)
        self.check_interval = timedelta(seconds=config.get("interval", 86400))

        # Local cache of device states
        self._devices: dict[str, Device] = {}

    async def initialize(self) -> None:
        """Load initial device states from API."""
        logger.info("Loading device registry from API")

        try:
            devices = await self.api_client.list_devices()
            for device in devices:
                self._devices[device.id] = device

            logger.info(f"Loaded {len(self._devices)} devices")

        except Exception as e:
            logger.error(f"Failed to load device registry: {e}")

    def get_device(self, device_id: str) -> Device | None:
        """Get device by ID."""
        return self._devices.get(device_id)

    def get_all_devices(self) -> list[Device]:
        """Get all registered devices."""
        return list(self._devices.values())

    def get_devices_by_status(self, status: DeviceStatus) -> list[Device]:
        """Get devices with a specific status."""
        return [d for d in self._devices.values() if d.status == status]

    def get_devices_needing_check(self) -> list[Device]:
        """
        Get devices that need a health check.

        A device needs a check if:
        - It has never been checked
        - It's been longer than check_interval since last check
        - It's not currently disabled
        """
        now = datetime.utcnow()
        needs_check = []

        for device in self._devices.values():
            # Skip disabled devices
            if device.status == DeviceStatus.DISABLED:
                continue

            # Never checked
            if device.last_check is None:
                needs_check.append(device)
                continue

            # Check if interval has passed
            time_since_check = now - device.last_check
            if time_since_check >= self.check_interval:
                needs_check.append(device)

        return needs_check

    async def register_device(
        self,
        device_id: str,
        lab_name: str,
        target: str,
        subtarget: str,
        profile: str | None = None,
        features: list[str] | None = None,
    ) -> Device:
        """
        Register a new device or update existing.

        Args:
            device_id: Unique device identifier
            lab_name: Lab that owns this device
            target: OpenWrt target
            subtarget: OpenWrt subtarget
            profile: Device profile (optional)
            features: Device features (optional)

        Returns:
            Registered device
        """
        device = Device(
            id=device_id,
            lab_name=lab_name,
            target=target,
            subtarget=subtarget,
            profile=profile,
            features=features or [],
            status=DeviceStatus.UNKNOWN,
        )

        # Register with API
        registered = await self.api_client.register_device(device)

        # Update local cache
        self._devices[device_id] = registered

        logger.info(f"Registered device: {device_id} in lab {lab_name}")
        return registered

    async def mark_healthy(self, device_id: str) -> Device:
        """
        Mark a device as healthy after successful check.

        Resets consecutive failures and updates timestamps.
        """
        device = self._devices.get(device_id)
        if not device:
            raise ValueError(f"Unknown device: {device_id}")

        now = datetime.utcnow()

        # Update via API
        updated = await self.api_client.update_device_status(
            device_id=device_id,
            status=DeviceStatus.HEALTHY.value,
            consecutive_failures=0,
        )

        # Update local cache
        updated.last_check = now
        updated.last_pass = now
        updated.consecutive_failures = 0
        updated.status = DeviceStatus.HEALTHY
        self._devices[device_id] = updated

        logger.info(f"Device {device_id} marked healthy")
        return updated

    async def record_failure(self, device_id: str, error: str | None = None) -> Device:
        """
        Record a health check failure.

        Increments consecutive failures and may change status.

        Returns:
            Updated device with new status
        """
        device = self._devices.get(device_id)
        if not device:
            raise ValueError(f"Unknown device: {device_id}")

        failures = device.consecutive_failures + 1
        now = datetime.utcnow()

        # Determine new status
        if failures >= self.disable_threshold:
            new_status = DeviceStatus.DISABLED
            logger.warning(f"Device {device_id} disabled after {failures} failures")
        elif failures >= self.warning_threshold:
            new_status = DeviceStatus.FAILING
            logger.warning(f"Device {device_id} failing ({failures} consecutive failures)")
        else:
            new_status = device.status

        # Update via API
        updated = await self.api_client.update_device_status(
            device_id=device_id,
            status=new_status.value,
            consecutive_failures=failures,
        )

        # Update local cache
        updated.last_check = now
        updated.consecutive_failures = failures
        updated.status = new_status
        self._devices[device_id] = updated

        return updated

    async def enable_device(self, device_id: str) -> Device:
        """
        Re-enable a disabled device.

        Called after manual intervention and successful health check.
        """
        device = self._devices.get(device_id)
        if not device:
            raise ValueError(f"Unknown device: {device_id}")

        # Update via API
        updated = await self.api_client.update_device_status(
            device_id=device_id,
            status=DeviceStatus.HEALTHY.value,
            consecutive_failures=0,
        )

        # Update local cache
        updated.status = DeviceStatus.HEALTHY
        updated.consecutive_failures = 0
        self._devices[device_id] = updated

        logger.info(f"Device {device_id} re-enabled")
        return updated

    async def disable_device(self, device_id: str, reason: str | None = None) -> Device:
        """
        Manually disable a device.

        Args:
            device_id: Device to disable
            reason: Optional reason for disabling
        """
        device = self._devices.get(device_id)
        if not device:
            raise ValueError(f"Unknown device: {device_id}")

        # Update via API
        updated = await self.api_client.update_device_status(
            device_id=device_id,
            status=DeviceStatus.DISABLED.value,
        )

        # Update local cache
        updated.status = DeviceStatus.DISABLED
        self._devices[device_id] = updated

        logger.info(f"Device {device_id} disabled: {reason or 'no reason provided'}")
        return updated

    def get_health_summary(self) -> dict[str, Any]:
        """Get a summary of device health across all labs."""
        summary = {
            "total": len(self._devices),
            "healthy": 0,
            "failing": 0,
            "disabled": 0,
            "unknown": 0,
            "by_lab": {},
        }

        for device in self._devices.values():
            # Count by status
            if device.status == DeviceStatus.HEALTHY:
                summary["healthy"] += 1
            elif device.status == DeviceStatus.FAILING:
                summary["failing"] += 1
            elif device.status == DeviceStatus.DISABLED:
                summary["disabled"] += 1
            else:
                summary["unknown"] += 1

            # Count by lab
            lab = device.lab_name
            if lab not in summary["by_lab"]:
                summary["by_lab"][lab] = {
                    "total": 0,
                    "healthy": 0,
                    "failing": 0,
                    "disabled": 0,
                }
            summary["by_lab"][lab]["total"] += 1
            summary["by_lab"][lab][device.status.value] += 1

        return summary
