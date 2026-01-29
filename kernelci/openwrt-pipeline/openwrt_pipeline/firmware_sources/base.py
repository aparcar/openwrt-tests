"""
Base class for firmware sources.
"""

import hashlib
import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator

from ..models import Firmware

logger = logging.getLogger(__name__)


def generate_firmware_id(
    *parts: str,
    hash_input: str | None = None,
) -> str:
    """
    Generate a unique firmware ID from parts.

    Args:
        *parts: ID components (e.g., "openwrt", version, target)
        hash_input: Optional string to hash for uniqueness suffix

    Returns:
        Colon-separated ID with optional hash suffix
    """
    base_id = ":".join(p for p in parts if p)
    if hash_input:
        short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        return f"{base_id}:{short_hash}"
    return base_id


def detect_firmware_type(filename: str) -> str | None:
    """
    Detect firmware type from filename.

    Returns:
        Firmware type (sysupgrade, factory, initramfs) or None
    """
    filename_lower = filename.lower()

    if "sysupgrade" in filename_lower:
        return "sysupgrade"
    elif "factory" in filename_lower:
        return "factory"
    elif "initramfs" in filename_lower or "kernel" in filename_lower:
        return "initramfs"
    elif filename_lower.endswith((".bin", ".img", ".itb")):
        return "unknown"
    return None


class FirmwareSource(ABC):
    """
    Abstract base class for firmware sources.

    Subclasses implement specific source types (official, PR, custom, etc.)
    """

    def __init__(self, name: str, config: dict):
        """
        Initialize firmware source.

        Args:
            name: Source identifier
            config: Source configuration from pipeline.yaml
        """
        self.name = name
        self.config = config
        self.enabled = config.get("enabled", True)

    @abstractmethod
    async def scan(self) -> AsyncIterator[Firmware]:
        """
        Scan for new firmware.

        Yields:
            Firmware objects for each discovered firmware image
        """
        pass

    @abstractmethod
    async def download_artifact(
        self,
        firmware: Firmware,
        artifact_type: str,
        destination: str,
    ) -> str:
        """
        Download a firmware artifact.

        Args:
            firmware: Firmware metadata
            artifact_type: Type of artifact (sysupgrade, factory, etc.)
            destination: Local path to save the artifact

        Returns:
            Path to the downloaded file
        """
        pass

    def is_enabled(self) -> bool:
        """Check if this source is enabled."""
        return self.enabled

    def get_check_interval(self) -> int:
        """Get the interval between scans in seconds."""
        return self.config.get("check_interval", 3600)

    async def initialize(self) -> None:
        """Initialize the source (called once at startup)."""
        logger.info(f"Initializing firmware source: {self.name}")

    async def cleanup(self) -> None:
        """Cleanup resources (called at shutdown)."""
        logger.info(f"Cleaning up firmware source: {self.name}")
