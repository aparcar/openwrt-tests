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
        if self.tags:
            # Check common tag names for device type
            if "device_type" in self.tags:
                return self.tags["device_type"]
            if "device" in self.tags:
                return self.tags["device"]
        # Fallback: try to extract from name (unreliable, prefer tags)
        parts = self.name.split("-", 1)
        if len(parts) > 1:
            device_part = parts[1]
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
        import os

        env = os.environ.copy()
        env["LG_COORDINATOR"] = self.coordinator_url

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

        # Use -v for verbose output with all place details
        returncode, stdout, stderr = await self._run_labgrid_client("-v", "places")

        if returncode != 0:
            logger.warning(f"Failed to list places: {stderr}")
            return self._places_cache or {}

        places = self._parse_verbose_places_output(stdout)
        self._places_cache = places
        self._cache_time = now

        return places

    def _parse_verbose_places_output(self, output: str) -> dict[str, Place]:
        """
        Parse output of 'labgrid-client -v places' command.

        Example output:
        Place 'labgrid-aparcar-openwrt_one':
          tags: device=openwrt_one
          matches:
            */labgrid-aparcar-openwrt_one/*
          acquired: None
          acquired resources:
          created: 2025-12-17 23:56:47
          changed: 2026-02-03 01:48:12.311304
        Place 'labgrid-aparcar-rpi-4':
          tags: device=rpi-4
          ...
        """
        places = {}
        current_name = None
        current_tags = {}
        current_acquired = False
        current_acquired_by = None

        for line in output.split("\n"):
            # New place starts with "Place '"
            if line.startswith("Place '"):
                # Save previous place
                if current_name:
                    places[current_name] = Place(
                        name=current_name,
                        acquired=current_acquired,
                        acquired_by=current_acquired_by,
                        tags=current_tags if current_tags else None,
                    )

                # Parse new place name
                match = re.match(r"Place '([^']+)':", line)
                if match:
                    current_name = match.group(1)
                    current_tags = {}
                    current_acquired = False
                    current_acquired_by = None

            elif current_name:
                line = line.strip()

                # Parse tags line: "tags: device=openwrt_one key2=value2"
                if line.startswith("tags:"):
                    tag_str = line.split(":", 1)[1].strip()
                    if tag_str:
                        for tag in tag_str.split():
                            if "=" in tag:
                                k, v = tag.split("=", 1)
                                current_tags[k] = v

                # Parse acquired line
                elif line.startswith("acquired:"):
                    value = line.split(":", 1)[1].strip()
                    if value and value != "None":
                        current_acquired = True
                        current_acquired_by = value

        # Don't forget the last place
        if current_name:
            places[current_name] = Place(
                name=current_name,
                acquired=current_acquired,
                acquired_by=current_acquired_by,
                tags=current_tags if current_tags else None,
            )

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

    async def get_places_for_lab(
        self, lab_name: str, refresh: bool = False
    ) -> list[Place]:
        """
        Get all places belonging to a specific lab.

        Filters places by:
        1. tags.lab == lab_name (explicit tag)
        2. place.name starts with "{lab_name}-" (naming convention)

        Args:
            lab_name: The lab name to filter by
            refresh: Force refresh of cached data

        Returns:
            List of Place objects belonging to this lab
        """
        places = await self.get_places(refresh=refresh)
        lab_places = []

        for place in places.values():
            # Check explicit lab tag first
            if place.tags and place.tags.get("lab") == lab_name:
                lab_places.append(place)
            # Fall back to name prefix matching
            elif place.name.startswith(f"{lab_name}-"):
                lab_places.append(place)

        return lab_places

    def get_unique_device_types(self, places: list[Place]) -> set[str]:
        """
        Extract unique device types from a list of places.

        Args:
            places: List of Place objects

        Returns:
            Set of unique device type strings
        """
        return {p.device_type for p in places if p.device_type}
