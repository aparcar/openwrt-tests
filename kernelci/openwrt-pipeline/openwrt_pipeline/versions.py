"""
OpenWrt Version Discovery

Fetches version information from downloads.openwrt.org/.versions.json
to automatically determine which branches to test:
- main (SNAPSHOT)
- stable (current release)
- oldstable (previous release series)

Reference: https://github.com/openwrt/firmware-selector-openwrt-org
"""

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

VERSIONS_URL = "https://downloads.openwrt.org/.versions.json"
DOWNLOADS_BASE = "https://downloads.openwrt.org"

# Tree configuration
OPENWRT_TREE = "openwrt"
OPENWRT_REPO = "https://git.openwrt.org/openwrt/openwrt.git"


@dataclass
class BranchInfo:
    """Information about an OpenWrt branch."""

    name: str  # Branch name (e.g., "main", "openwrt-24.10")
    version: str  # Version string (e.g., "SNAPSHOT", "24.10.0")
    url: str  # Base URL for firmware downloads
    is_snapshot: bool = False


def version_to_branch(version: str) -> str:
    """Convert version string to git branch name."""
    if version.upper() == "SNAPSHOT":
        return "main"
    # Extract major.minor (e.g., "24.10.0" -> "openwrt-24.10")
    match = re.match(r"(\d+)\.(\d+)", version)
    if match:
        return f"openwrt-{match.group(1)}.{match.group(2)}"
    return "main"


def extract_major_minor(version: str) -> tuple[int, int] | None:
    """Extract major.minor as tuple for sorting."""
    match = re.match(r"(\d+)\.(\d+)", version)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    return None


async def fetch_versions(
    timeout: float = 30.0,
) -> dict:
    """
    Fetch version info from OpenWrt downloads server.

    Returns:
        Dict with 'stable_version' and 'versions_list'
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(VERSIONS_URL)
        response.raise_for_status()
        return response.json()


async def get_active_branches(
    include_snapshot: bool = True,
    include_oldstable: bool = True,
    include_upcoming: bool = True,
) -> list[BranchInfo]:
    """
    Get list of active branches to test.

    Fetches .versions.json and returns:
    - main (SNAPSHOT) - always latest development
    - upcoming - release candidate (e.g., 25.12.0-rc4)
    - stable - current release (e.g., 24.10.0)
    - oldstable - previous release series (e.g., 23.05.5)

    Returns:
        List of BranchInfo objects
    """
    branches = []

    # Always include snapshot/main
    if include_snapshot:
        branches.append(
            BranchInfo(
                name="main",
                version="SNAPSHOT",
                url=f"{DOWNLOADS_BASE}/snapshots/targets",
                is_snapshot=True,
            )
        )

    try:
        data = await fetch_versions()
        stable_version = data.get("stable_version", "")
        upcoming_version = data.get("upcoming_version", "")
        versions_list = data.get("versions_list", [])

        logger.info(f"Fetched versions: stable={stable_version}, upcoming={upcoming_version}, all={versions_list}")

        # Add upcoming version (release candidate) if available
        if include_upcoming and upcoming_version:
            branches.append(
                BranchInfo(
                    name=version_to_branch(upcoming_version),
                    version=upcoming_version,
                    url=f"{DOWNLOADS_BASE}/releases/{upcoming_version}/targets",
                )
            )

        # Add stable version
        if stable_version:
            branches.append(
                BranchInfo(
                    name=version_to_branch(stable_version),
                    version=stable_version,
                    url=f"{DOWNLOADS_BASE}/releases/{stable_version}/targets",
                )
            )

        # Find oldstable (previous major.minor series)
        if include_oldstable and versions_list:
            stable_mm = extract_major_minor(stable_version)

            # Sort versions and find previous series
            versioned = []
            for v in versions_list:
                mm = extract_major_minor(v)
                if mm:
                    versioned.append((mm, v))

            # Sort by major.minor descending
            versioned.sort(key=lambda x: x[0], reverse=True)

            # Find first version from a different series than stable
            for mm, version in versioned:
                if stable_mm and mm[0:2] != stable_mm[0:2]:
                    # Different major.minor series = oldstable
                    branches.append(
                        BranchInfo(
                            name=version_to_branch(version),
                            version=version,
                            url=f"{DOWNLOADS_BASE}/releases/{version}/targets",
                        )
                    )
                    break

    except Exception as e:
        logger.warning(f"Failed to fetch versions, using defaults: {e}")
        # Fallback to known versions
        branches.extend(
            [
                BranchInfo(
                    name="openwrt-24.10",
                    version="24.10.0",
                    url=f"{DOWNLOADS_BASE}/releases/24.10.0/targets",
                ),
                BranchInfo(
                    name="openwrt-23.05",
                    version="23.05.5",
                    url=f"{DOWNLOADS_BASE}/releases/23.05.5/targets",
                ),
            ]
        )

    return branches


def get_tree_info() -> dict:
    """Get tree information for KernelCI nodes."""
    return {
        "tree": OPENWRT_TREE,
        "url": OPENWRT_REPO,
    }
