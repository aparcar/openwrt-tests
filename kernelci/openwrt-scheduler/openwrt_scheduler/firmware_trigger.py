"""
Firmware Trigger Service

Main service that watches all configured firmware sources and
creates firmware entries in the KernelCI API when new images
are detected.

This service runs continuously and:
1. Periodically scans configured sources for new firmware
2. Creates firmware entries in the API
3. Publishes events for the scheduler to create test jobs
"""

import asyncio
import io
import logging
from datetime import datetime

import httpx
import structlog
import uvicorn
from fastapi import FastAPI
from minio import Minio

logging.basicConfig(format="%(message)s", level=logging.INFO)

from .api_client import APIError, KernelCIClient
from .config import load_pipeline_config, settings
from .firmware_sources.official import OfficialReleaseSource
from .versions import get_active_branches, version_to_branch

# Configure logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


class FirmwareTriggerService:
    """
    Service that monitors firmware sources and triggers test jobs.
    """

    def __init__(self):
        self.config = load_pipeline_config()
        self.sources = []
        self.api_client: KernelCIClient | None = None
        self._minio: Minio | None = None
        self._http_client: httpx.AsyncClient | None = None
        self.running = False
        self._tasks: list[asyncio.Task] = []

    async def initialize(self) -> None:
        """Initialize all firmware sources and API client."""
        logger.info("Initializing Firmware Trigger Service")

        # Initialize API client
        self.api_client = KernelCIClient()
        await self.api_client.connect()

        # Initialize HTTP client for firmware downloads
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),
            follow_redirects=True,
        )

        # Initialize MinIO client for firmware storage
        if settings.minio_endpoint and settings.minio_access_key:
            self._minio = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
            bucket = settings.minio_firmware_bucket
            if not self._minio.bucket_exists(bucket):
                self._minio.make_bucket(bucket)
                logger.info(f"Created MinIO bucket: {bucket}")
            logger.info("MinIO storage initialized for firmware mirroring")
        else:
            logger.warning(
                "MinIO not configured — firmware URLs will point to upstream"
            )

        # Initialize firmware sources
        sources_config = self.config.get("firmware_sources", {})

        # Dynamically fetch active branches from .versions.json
        official_config = sources_config.get("official", {})
        if official_config.get("enabled", True):
            await self._init_official_sources(official_config)

        logger.info(f"Initialized {len(self.sources)} firmware sources")

    async def _init_official_sources(self, config: dict) -> None:
        """
        Initialize official release sources dynamically.

        Fetches active branches from .versions.json and creates
        a source for each (main/SNAPSHOT, stable, oldstable).
        """
        # Get targets to scan from config
        default_targets = config.get("targets", [])
        check_interval = config.get("check_interval", 3600)

        # Fetch active branches dynamically
        try:
            branches = await get_active_branches(
                include_snapshot=config.get("include_snapshot", True),
                include_oldstable=config.get("include_oldstable", True),
            )
            logger.info(f"Discovered {len(branches)} active branches")
        except Exception as e:
            logger.error(f"Failed to fetch branches: {e}")
            return

        # Create a source for each branch
        for branch in branches:
            source_config = {
                "enabled": True,
                "type": "openwrt_releases",
                "sources": {
                    branch.name: {
                        "url": branch.url,
                        "version": branch.version,
                        "branch": branch.name,
                        "check_interval": check_interval,
                        "targets": default_targets,
                    }
                },
            }

            source = OfficialReleaseSource(f"official-{branch.name}", source_config)
            await source.initialize()
            self.sources.append(source)
            logger.info(
                f"Initialized source for {branch.name} "
                f"(version={branch.version}, url={branch.url})"
            )

    async def shutdown(self) -> None:
        """Cleanup resources."""
        logger.info("Shutting down Firmware Trigger Service")

        self.running = False

        # Cancel running tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Cleanup sources
        for source in self.sources:
            await source.cleanup()

        # Close API client
        if self.api_client:
            await self.api_client.close()

        # Close HTTP client
        if self._http_client:
            await self._http_client.aclose()

    async def run(self) -> None:
        """Main service loop."""
        self.running = True

        # Start a scan task for each source
        for source in self.sources:
            if source.is_enabled():
                task = asyncio.create_task(
                    self._source_scan_loop(source),
                    name=f"scan-{source.name}",
                )
                self._tasks.append(task)

        # Wait for all tasks
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _source_scan_loop(self, source) -> None:
        """Continuously scan a firmware source."""
        logger.info(f"Starting scan loop for source: {source.name}")

        while self.running:
            try:
                await self._scan_source(source)
            except Exception as e:
                logger.exception(f"Error scanning source {source.name}", error=str(e))

            # Wait for next scan
            interval = source.get_check_interval()
            logger.debug(f"Next scan for {source.name} in {interval} seconds")
            await asyncio.sleep(interval)

    async def _mirror_to_storage(self, url: str, object_path: str) -> str | None:
        """
        Download firmware from upstream URL and upload to MinIO.

        Returns the public URL of the mirrored file, or None if mirroring
        is not configured or fails (caller should fall back to original URL).
        """
        if not self._minio or not self._http_client or not settings.storage_url:
            return None

        bucket = settings.minio_firmware_bucket
        try:
            # Check if already mirrored
            try:
                self._minio.stat_object(bucket, object_path)
                public_url = f"{settings.storage_url}/{bucket}/{object_path}"
                logger.debug("Firmware already mirrored", object_path=object_path)
                return public_url
            except Exception:
                pass  # Not found, proceed with download

            logger.info("Downloading firmware for mirroring", url=url)
            response = await self._http_client.get(url)
            response.raise_for_status()
            data = response.content
            content_type = response.headers.get(
                "content-type", "application/octet-stream"
            )

            self._minio.put_object(
                bucket_name=bucket,
                object_name=object_path,
                data=io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )

            public_url = f"{settings.storage_url}/{bucket}/{object_path}"
            logger.info(
                "Firmware mirrored to storage",
                object_path=object_path,
                size_mb=round(len(data) / 1024 / 1024, 1),
            )
            return public_url

        except Exception as e:
            logger.warning(f"Failed to mirror firmware: {e}", url=url)
            return None

    async def _mirror_artifacts(
        self, artifacts: dict[str, str], branch: str, version: str,
        target: str, subtarget: str,
    ) -> dict[str, str]:
        """
        Mirror all artifact URLs to MinIO storage.

        Returns a new artifacts dict with mirrored URLs (falls back to
        original URLs if mirroring fails).
        """
        mirrored = {}
        for artifact_type, url in artifacts.items():
            filename = url.split("/")[-1]
            object_path = f"{branch}/{version}/{target}/{subtarget}/{filename}"
            mirrored_url = await self._mirror_to_storage(url, object_path)
            mirrored[artifact_type] = mirrored_url or url
        return mirrored

    async def _scan_source(self, source) -> None:
        """Scan a source and create firmware entries."""
        logger.info(f"Scanning firmware source: {source.name}")
        scan_start = datetime.utcnow()
        new_count = 0
        existing_count = 0

        async for firmware in source.scan():
            try:
                # Check if firmware already exists by querying for a
                # kbuild node with the same name and commit
                node_name = (
                    f"openwrt-{firmware.target}-{firmware.subtarget}-{firmware.profile}"
                )
                existing = await self.api_client.query_nodes(
                    kind="kbuild",
                    name=node_name,
                    limit=1,
                    **{
                        "data.kernel_revision.commit": firmware.git_commit_hash,
                    },
                )
                if existing:
                    existing_count += 1
                    continue

                # Create firmware entry
                logger.info(
                    "New firmware found",
                    firmware_id=firmware.id,
                    version=firmware.version,
                    target=firmware.target,
                    profile=firmware.profile,
                )

                artifacts = {}
                if firmware.artifacts:
                    if firmware.artifacts.sysupgrade:
                        artifacts["sysupgrade"] = firmware.artifacts.sysupgrade
                    if firmware.artifacts.factory:
                        artifacts["factory"] = firmware.artifacts.factory
                    if firmware.artifacts.initramfs:
                        artifacts["initramfs"] = firmware.artifacts.initramfs
                    if firmware.artifacts.combined:
                        artifacts["combined"] = firmware.artifacts.combined

                # Mirror firmware images to MinIO storage
                branch = version_to_branch(firmware.version)
                artifacts = await self._mirror_artifacts(
                    artifacts, branch, firmware.version,
                    firmware.target, firmware.subtarget,
                )

                await self.api_client.create_firmware_node(
                    name=node_name,
                    version=firmware.version,
                    target=firmware.target,
                    subtarget=firmware.subtarget,
                    profile=firmware.profile,
                    source=firmware.source.value,
                    artifacts=artifacts,
                    git_commit=firmware.git_commit_hash,
                )
                new_count += 1

            except APIError as e:
                if e.status_code == 409:  # Conflict - already exists
                    existing_count += 1
                else:
                    logger.error(
                        "API error creating firmware",
                        firmware_id=firmware.id,
                        error=str(e),
                    )
            except Exception as e:
                logger.exception(
                    "Error processing firmware",
                    firmware_id=firmware.id,
                    error=str(e),
                )

        scan_duration = (datetime.utcnow() - scan_start).total_seconds()
        logger.info(
            "Scan complete",
            source=source.name,
            new_firmware=new_count,
            existing_firmware=existing_count,
            duration_seconds=scan_duration,
        )


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="OpenWrt Firmware Trigger",
    description="Firmware source watcher and upload handler for OpenWrt KernelCI",
    version="0.1.0",
)

# Service instance
_service: FirmwareTriggerService | None = None


@app.on_event("startup")
async def startup():
    """Initialize service on startup."""
    global _service
    _service = FirmwareTriggerService()
    await _service.initialize()

    # Start the scan loop in background
    asyncio.create_task(_service.run())


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    if _service:
        await _service.shutdown()


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "sources": len(_service.sources) if _service else 0}


@app.get("/sources")
async def list_sources():
    """List configured firmware sources."""
    if not _service:
        return {"sources": []}

    return {
        "sources": [
            {
                "name": s.name,
                "enabled": s.is_enabled(),
                "check_interval": s.get_check_interval(),
            }
            for s in _service.sources
        ]
    }


# =============================================================================
# Main Entry Point
# =============================================================================


def main():
    """Main entry point for the firmware trigger service."""
    # Handle signals
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Run with uvicorn for API endpoints
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8080,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)

    try:
        loop.run_until_complete(server.serve())
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
