"""
Official OpenWrt release firmware source.

Watches downloads.openwrt.org for new firmware images across:
- Snapshots (development builds)
- Stable releases
- Old stable releases
"""

import hashlib
import logging
from pathlib import Path
from typing import AsyncIterator

import httpx

from ..models import Firmware, FirmwareArtifacts
from ..models import FirmwareSource as FirmwareSourceEnum
from .base import FirmwareSource, generate_firmware_id

logger = logging.getLogger(__name__)


class OfficialReleaseSource(FirmwareSource):
    """
    Firmware source for official OpenWrt releases.

    Scans downloads.openwrt.org for profiles.json files and extracts
    firmware metadata for each supported device profile.
    """

    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        self.sources = config.get("sources", {})
        self._http_client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        """Initialize HTTP client."""
        await super().initialize()
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )

    async def cleanup(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
        await super().cleanup()

    @property
    def client(self) -> httpx.AsyncClient:
        """Get HTTP client."""
        if self._http_client is None:
            raise RuntimeError("Source not initialized")
        return self._http_client

    async def scan(self) -> AsyncIterator[Firmware]:
        """
        Scan all configured release sources for firmware.

        Yields firmware objects for each profile found in profiles.json.
        """
        for source_name, source_config in self.sources.items():
            if not source_config.get("enabled", True):
                logger.debug(f"Skipping disabled source: {source_name}")
                continue

            logger.info(f"Scanning official source: {source_name}")

            base_url = source_config["url"]
            version = source_config["version"]
            targets = source_config.get("targets", [])

            if targets:
                # Scan specific targets
                for target_path in targets:
                    target, subtarget = target_path.split("/")
                    async for firmware in self._scan_target(
                        base_url, version, target, subtarget, source_name
                    ):
                        yield firmware
            else:
                # Scan all targets (slower)
                async for firmware in self._scan_all_targets(
                    base_url, version, source_name
                ):
                    yield firmware

    async def _scan_target(
        self,
        base_url: str,
        version: str,
        target: str,
        subtarget: str,
        source_name: str,
    ) -> AsyncIterator[Firmware]:
        """Scan a specific target/subtarget for firmware."""
        profiles_url = f"{base_url}/{target}/{subtarget}/profiles.json"

        try:
            response = await self.client.get(profiles_url)
            response.raise_for_status()
            profiles_data = response.json()
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch profiles from {profiles_url}: {e}")
            return
        except Exception as e:
            logger.error(f"Error parsing profiles from {profiles_url}: {e}")
            return

        # Extract git commit from version_code if available
        version_code = profiles_data.get("version_code", "")
        git_commit = version_code.split("-")[-1] if "-" in version_code else None

        profiles = profiles_data.get("profiles", {})
        logger.info(f"Found {len(profiles)} profiles for {target}/{subtarget}")

        for profile_name, profile_data in profiles.items():
            firmware = self._create_firmware(
                base_url=base_url,
                version=version,
                target=target,
                subtarget=subtarget,
                profile_name=profile_name,
                profile_data=profile_data,
                source_name=source_name,
                git_commit=git_commit,
            )
            if firmware:
                yield firmware

    async def _scan_all_targets(
        self,
        base_url: str,
        version: str,
        source_name: str,
    ) -> AsyncIterator[Firmware]:
        """Scan all available targets (by listing directory)."""
        # Full target scan not implemented - would require directory listing
        # which downloads.openwrt.org doesn't support well
        logger.warning(
            f"Full target scan not implemented for {source_name}. "
            "Please configure specific targets in pipeline.yaml"
        )
        # Empty async generator
        if False:
            yield  # type: ignore

    def _create_firmware(
        self,
        base_url: str,
        version: str,
        target: str,
        subtarget: str,
        profile_name: str,
        profile_data: dict,
        source_name: str,
        git_commit: str | None = None,
    ) -> Firmware | None:
        """Create a Firmware object from profile data."""
        images = profile_data.get("images", [])
        if not images:
            return None

        # Build artifact URLs
        artifacts = FirmwareArtifacts()
        for image in images:
            image_type = image.get("type", "").lower()
            filename = image.get("name")
            sha256 = image.get("sha256")

            if not filename:
                continue

            url = f"{base_url}/{target}/{subtarget}/{filename}"

            # Map image types to artifact fields
            if "sysupgrade" in image_type:
                artifacts.sysupgrade = url
                artifacts.sysupgrade_sha256 = sha256
            elif "factory" in image_type:
                artifacts.factory = url
                artifacts.factory_sha256 = sha256
            elif "initramfs" in image_type or "kernel" in image_type:
                artifacts.initramfs = url
                artifacts.initramfs_sha256 = sha256
            elif "combined" in image_type and artifacts.combined is None:
                # x86 targets use combined images (prefer combined-efi)
                artifacts.combined = url
                artifacts.combined_sha256 = sha256

        # Need at least one usable image
        if not (
            artifacts.sysupgrade
            or artifacts.factory
            or artifacts.initramfs
            or artifacts.combined
        ):
            return None

        # Generate firmware ID
        firmware_id = self._generate_firmware_id(
            version, target, subtarget, profile_name, git_commit
        )

        # Extract features from device packages
        features = self._extract_features(profile_data.get("device_packages", []))

        return Firmware(
            id=firmware_id,
            source=FirmwareSourceEnum.OFFICIAL,
            source_url=f"{base_url}/{target}/{subtarget}/",
            source_ref=source_name,
            version=version,
            target=target,
            subtarget=subtarget,
            profile=profile_name,
            git_commit_hash=git_commit,
            artifacts=artifacts,
            features=features,
            packages=profile_data.get("device_packages", []),
        )

    def _generate_firmware_id(
        self,
        version: str,
        target: str,
        subtarget: str,
        profile: str,
        git_commit: str | None = None,
    ) -> str:
        """Generate a unique firmware ID."""
        parts = [
            "openwrt",
            version.lower().replace(".", "-"),
            target,
            subtarget,
            profile,
        ]
        if git_commit:
            parts.append(git_commit[:8])

        hash_input = f"{version}:{target}:{subtarget}:{profile}:{git_commit or ''}"
        return generate_firmware_id(*parts, hash_input=hash_input)

    def _extract_features(self, packages: list[str]) -> list[str]:
        """Extract device features from package list."""
        features = []

        # WiFi detection
        wifi_packages = ["hostapd", "wpad", "iw", "iwinfo"]
        if any(
            pkg in packages or any(pkg in p for p in packages) for pkg in wifi_packages
        ):
            features.append("wifi")

        # USB detection
        usb_packages = ["kmod-usb", "usbutils"]
        if any(any(pkg in p for p in packages) for pkg in usb_packages):
            features.append("usb")

        return features

    async def download_artifact(
        self,
        firmware: Firmware,
        artifact_type: str,
        destination: str,
    ) -> str:
        """Download a firmware artifact to local storage."""
        # Get artifact URL
        url = getattr(firmware.artifacts, artifact_type, None)
        if not url:
            raise ValueError(f"Artifact type '{artifact_type}' not found for firmware")

        dest_path = Path(destination)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Downloading {artifact_type} from {url}")

        async with self.client.stream("GET", url) as response:
            response.raise_for_status()

            with open(dest_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    f.write(chunk)

        # Verify checksum if available
        expected_sha256 = getattr(firmware.artifacts, f"{artifact_type}_sha256", None)
        if expected_sha256:
            actual_sha256 = self._calculate_sha256(dest_path)
            if actual_sha256 != expected_sha256:
                dest_path.unlink()
                raise ValueError(
                    f"Checksum mismatch: expected {expected_sha256}, "
                    f"got {actual_sha256}"
                )

        logger.info(f"Downloaded {artifact_type} to {dest_path}")
        return str(dest_path)

    def _calculate_sha256(self, file_path: Path) -> str:
        """Calculate SHA256 checksum of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
