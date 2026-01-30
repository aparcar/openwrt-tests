"""
ASU (Attended Sysupgrade) Client

Client for requesting custom OpenWrt firmware images from sysupgrade.openwrt.org.
Allows building images with additional packages for specific test types
(e.g., kselftest images with bash, python3, etc.).

API Documentation: https://sysupgrade.openwrt.org/docs
"""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Default ASU server
ASU_API_URL = "https://sysupgrade.openwrt.org/api/v1"

# Timeout for build requests (can take a while)
BUILD_TIMEOUT = 600  # 10 minutes
POLL_INTERVAL = 10  # seconds


@dataclass
class ImageBuildRequest:
    """Request for a custom OpenWrt image build."""

    target: str
    subtarget: str
    profile: str
    version: str
    packages: list[str] = field(default_factory=list)
    filesystem: str | None = None
    diff_packages: bool = False

    def to_dict(self) -> dict:
        """Convert to API request format."""
        data = {
            "target": self.target,
            "subtarget": self.subtarget,
            "profile": self.profile,
            "version": self.version,
            "packages": self.packages,
            "diff_packages": self.diff_packages,
        }
        if self.filesystem:
            data["filesystem"] = self.filesystem
        return data


@dataclass
class ImageBuildResult:
    """Result of a custom image build."""

    request_hash: str
    status: str  # "queued", "building", "completed", "failed"
    version: str
    target: str
    profile: str

    # Available when completed
    sysupgrade_url: str | None = None
    factory_url: str | None = None
    manifest_url: str | None = None
    sha256_sysupgrade: str | None = None
    sha256_factory: str | None = None

    # Error info if failed
    error: str | None = None

    @classmethod
    def from_response(cls, data: dict) -> "ImageBuildResult":
        """Create from API response."""
        # Extract image URLs from response
        images = data.get("images", [])
        sysupgrade_url = None
        factory_url = None
        sha256_sysupgrade = None
        sha256_factory = None

        for img in images:
            img_type = img.get("type", "")
            if "sysupgrade" in img_type:
                sysupgrade_url = img.get("url")
                sha256_sysupgrade = img.get("sha256")
            elif "factory" in img_type:
                factory_url = img.get("url")
                sha256_factory = img.get("sha256")

        return cls(
            request_hash=data.get("request_hash", ""),
            status=data.get("status", "unknown"),
            version=data.get("version", ""),
            target=data.get("target", ""),
            profile=data.get("profile", ""),
            sysupgrade_url=sysupgrade_url,
            factory_url=factory_url,
            manifest_url=data.get("manifest_url"),
            sha256_sysupgrade=sha256_sysupgrade,
            sha256_factory=sha256_factory,
            error=data.get("error"),
        )


class ASUClient:
    """
    Client for OpenWrt Attended Sysupgrade API.

    Requests custom firmware builds with specified packages.
    Handles build queuing and polling for completion.
    """

    def __init__(self, api_url: str = ASU_API_URL):
        self.api_url = api_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ASUClient":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def connect(self) -> None:
        """Create HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
        )

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("ASUClient not connected")
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def _request(
        self, method: str, path: str, **kwargs
    ) -> dict:
        """Make API request with retry."""
        url = f"{self.api_url}{path}"
        response = await self.client.request(method, url, **kwargs)

        if response.status_code >= 400:
            logger.error(f"ASU API error: {response.status_code} - {response.text}")
            response.raise_for_status()

        return response.json()

    async def get_overview(self) -> dict:
        """Get available versions, targets, and profiles."""
        return await self._request("GET", "/overview")

    async def request_build(
        self, request: ImageBuildRequest
    ) -> ImageBuildResult:
        """
        Request a custom image build.

        Returns immediately with build status. If the image is cached,
        returns completed status with URLs. Otherwise returns queued status.
        """
        logger.info(
            f"Requesting build: {request.target}/{request.subtarget}/{request.profile} "
            f"v{request.version} with packages: {request.packages}"
        )

        data = await self._request("POST", "/build", json=request.to_dict())
        result = ImageBuildResult.from_response(data)

        logger.info(f"Build request {result.request_hash}: {result.status}")
        return result

    async def get_build_status(self, request_hash: str) -> ImageBuildResult:
        """Check status of a build request."""
        data = await self._request("GET", f"/build/{request_hash}")
        return ImageBuildResult.from_response(data)

    async def build_and_wait(
        self,
        request: ImageBuildRequest,
        timeout: float = BUILD_TIMEOUT,
        poll_interval: float = POLL_INTERVAL,
    ) -> ImageBuildResult:
        """
        Request a build and wait for completion.

        Args:
            request: Build request specification
            timeout: Maximum time to wait (seconds)
            poll_interval: Time between status checks (seconds)

        Returns:
            Completed build result with image URLs

        Raises:
            TimeoutError: If build doesn't complete in time
            RuntimeError: If build fails
        """
        result = await self.request_build(request)

        if result.status == "completed":
            return result

        if result.status == "failed":
            raise RuntimeError(f"Build failed: {result.error}")

        # Poll for completion
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            result = await self.get_build_status(result.request_hash)
            logger.debug(f"Build {result.request_hash}: {result.status}")

            if result.status == "completed":
                logger.info(
                    f"Build completed: {result.sysupgrade_url or result.factory_url}"
                )
                return result

            if result.status == "failed":
                raise RuntimeError(f"Build failed: {result.error}")

        raise TimeoutError(
            f"Build {result.request_hash} did not complete within {timeout}s"
        )


# Convenience function for one-off builds
async def build_custom_image(
    target: str,
    subtarget: str,
    profile: str,
    version: str,
    packages: list[str],
    api_url: str = ASU_API_URL,
    timeout: float = BUILD_TIMEOUT,
) -> ImageBuildResult:
    """
    Build a custom OpenWrt image with specified packages.

    Args:
        target: Hardware target (e.g., "ath79")
        subtarget: Subtarget (e.g., "generic")
        profile: Device profile (e.g., "tplink_archer-c7-v2")
        version: OpenWrt version (e.g., "SNAPSHOT", "23.05.3")
        packages: List of packages to include
        api_url: ASU API URL
        timeout: Build timeout in seconds

    Returns:
        Build result with image URLs
    """
    request = ImageBuildRequest(
        target=target,
        subtarget=subtarget,
        profile=profile,
        version=version,
        packages=packages,
    )

    async with ASUClient(api_url) as client:
        return await client.build_and_wait(request, timeout=timeout)
