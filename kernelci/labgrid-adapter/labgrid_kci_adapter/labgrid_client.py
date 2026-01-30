"""
Labgrid Coordinator Client

Provides async interface to query the labgrid coordinator for
available places and their status.
"""

import asyncio
import logging
import re
from dataclasses import dataclass

from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class Place:
    """Represents a labgrid place (physical device)."""

    name: str
    acquired: bool
    acquired_by: str | None = None
    tags: dict[str, str] | None = None

    @property
    def device_type(self) -> str | None:
        """Extract device type from place tags or name."""
        if self.tags and "device_type" in self.tags:
            return self.tags["device_type"]
        # Try to extract from name pattern: lab-devicetype or lab-devicetype-N
        # e.g., "aparcar-openwrt_one-1" -> "openwrt_one"
        parts = self.name.split("-", 1)
        if len(parts) > 1:
            # Remove trailing instance number if present
            device_part = parts[1]
            # Handle "openwrt_one-1" -> "openwrt_one"
            match = re.match(r"(.+?)(?:-\d+)?$", device_part)
            if match:
                return match.group(1)
        return None


class LabgridClient:
    """
    Client for interacting with the labgrid coordinator.

    Uses labgrid-client CLI commands to query place status.
    """

    def __init__(self, coordinator_url: str | None = None):
        self.coordinator_url = coordinator_url or settings.lg_coordinator
        self._places_cache: dict[str, Place] | None = None
        self._cache_time: float = 0
        self._cache_ttl: float = 5.0  # Cache places for 5 seconds

    async def _run_labgrid_client(self, *args: str) -> tuple[int, str, str]:
        """Run labgrid-client command."""
        env = {"LG_COORDINATOR": self.coordinator_url}

        proc = await asyncio.create_subprocess_exec(
            "labgrid-client",
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode(), stderr.decode()

    async def get_places(self, refresh: bool = False) -> dict[str, Place]:
        """
        Get all places from the coordinator.

        Args:
            refresh: Force refresh of cached data

        Returns:
            Dict mapping place name to Place object
        """
        now = asyncio.get_event_loop().time()

        if (
            not refresh
            and self._places_cache is not None
            and (now - self._cache_time) < self._cache_ttl
        ):
            return self._places_cache

        returncode, stdout, stderr = await self._run_labgrid_client("places")

        if returncode != 0:
            logger.warning(f"Failed to list places: {stderr}")
            return self._places_cache or {}

        places = self._parse_places_output(stdout)
        self._places_cache = places
        self._cache_time = now

        return places

    def _parse_places_output(self, output: str) -> dict[str, Place]:
        """
        Parse output of 'labgrid-client places' command.

        Example output:
        Place 'aparcar-openwrt_one':
          acquired: user/host
          tags:
            device_type: openwrt_one
        Place 'aparcar-openwrt_one-2':
          acquired:
          tags:
            device_type: openwrt_one
        """
        places = {}
        current_place = None
        current_tags = {}
        in_tags = False

        for line in output.split("\n"):
            line = line.rstrip()

            # New place
            if line.startswith("Place '"):
                # Save previous place
                if current_place:
                    places[current_place.name] = current_place
                    current_place.tags = current_tags if current_tags else None

                # Parse place name
                match = re.match(r"Place '([^']+)':", line)
                if match:
                    current_place = Place(
                        name=match.group(1),
                        acquired=False,
                        acquired_by=None,
                    )
                    current_tags = {}
                    in_tags = False

            elif current_place and line.strip().startswith("acquired:"):
                acquired_value = line.split(":", 1)[1].strip()
                current_place.acquired = bool(acquired_value)
                current_place.acquired_by = acquired_value if acquired_value else None
                in_tags = False

            elif current_place and line.strip() == "tags:":
                in_tags = True

            elif current_place and in_tags and ":" in line:
                # Parse tag
                key, value = line.strip().split(":", 1)
                current_tags[key.strip()] = value.strip()

        # Don't forget the last place
        if current_place:
            current_place.tags = current_tags if current_tags else None
            places[current_place.name] = current_place

        return places

    async def get_places_by_device_type(
        self, device_type: str, refresh: bool = False
    ) -> list[Place]:
        """
        Get all places for a specific device type.

        Args:
            device_type: The device type to filter by
            refresh: Force refresh of cached data

        Returns:
            List of Place objects matching the device type
        """
        places = await self.get_places(refresh=refresh)
        return [p for p in places.values() if p.device_type == device_type]

    async def get_available_places(
        self, device_type: str, refresh: bool = False
    ) -> list[Place]:
        """
        Get available (not acquired) places for a device type.

        Args:
            device_type: The device type to filter by
            refresh: Force refresh of cached data

        Returns:
            List of available Place objects
        """
        places = await self.get_places_by_device_type(device_type, refresh=refresh)
        return [p for p in places if not p.acquired]

    async def count_available(self, device_type: str, refresh: bool = False) -> int:
        """
        Count available places for a device type.

        Args:
            device_type: The device type to count
            refresh: Force refresh of cached data

        Returns:
            Number of available places
        """
        available = await self.get_available_places(device_type, refresh=refresh)
        return len(available)

    async def acquire_place(self, place_name: str) -> bool:
        """
        Acquire a specific place.

        Args:
            place_name: Name of the place to acquire

        Returns:
            True if successfully acquired
        """
        returncode, _, stderr = await self._run_labgrid_client(
            "-p", place_name, "acquire"
        )
        if returncode != 0:
            logger.warning(f"Failed to acquire {place_name}: {stderr}")
            return False
        return True

    async def release_place(self, place_name: str) -> bool:
        """
        Release a specific place.

        Args:
            place_name: Name of the place to release

        Returns:
            True if successfully released
        """
        returncode, _, stderr = await self._run_labgrid_client(
            "-p", place_name, "release"
        )
        if returncode != 0:
            logger.warning(f"Failed to release {place_name}: {stderr}")
            return False
        return True
