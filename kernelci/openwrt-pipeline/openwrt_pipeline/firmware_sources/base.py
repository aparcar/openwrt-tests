"""
Base class for firmware sources.
"""

import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator

from ..models import Firmware

logger = logging.getLogger(__name__)


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
