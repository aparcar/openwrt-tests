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
from datetime import datetime

import structlog
import uvicorn
from fastapi import FastAPI

from .api_client import APIError, KernelCIClient
from .config import load_pipeline_config, settings
from .firmware_sources import GitHubPRSource, OfficialReleaseSource
from .firmware_sources.custom import init_uploader
from .firmware_sources.custom import router as upload_router

from .versions import get_active_branches

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
        self.running = False
        self._tasks: list[asyncio.Task] = []

    async def initialize(self) -> None:
        """Initialize all firmware sources and API client."""
        logger.info("Initializing Firmware Trigger Service")

        # Initialize API client
        self.api_client = KernelCIClient()
        await self.api_client.connect()

        # Initialize firmware sources
        sources_config = self.config.get("firmware_sources", {})

        # Dynamically fetch active branches from .versions.json
        official_config = sources_config.get("official", {})
        if official_config.get("enabled", True):
            await self._init_official_sources(official_config)

        # GitHub PR source
        if "github_pr" in sources_config:
            source = GitHubPRSource("github_pr", sources_config["github_pr"])
            await source.initialize()
            if source.is_enabled():
                self.sources.append(source)
                logger.info("Initialized GitHub PR source")
            else:
                logger.warning("GitHub PR source disabled (no token)")

        # Custom upload source (initialized separately for FastAPI)
        if "custom" in sources_config:
            init_uploader(sources_config["custom"])
            logger.info("Initialized custom upload handler")

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

    async def _scan_source(self, source) -> None:
        """Scan a source and create firmware entries."""
        logger.info(f"Scanning firmware source: {source.name}")
        scan_start = datetime.utcnow()
        new_count = 0
        existing_count = 0

        async for firmware in source.scan():
            try:
                # Check if firmware already exists by querying for a
                # kbuild node with the same name
                node_name = (
                    f"openwrt-{firmware.target}-{firmware.subtarget}-{firmware.profile}"
                )
                existing = await self.api_client.query_nodes(
                    kind="kbuild",
                    name=node_name,
                    limit=1,
                )
                # Match on git commit to detect new builds of same profile
                if existing and any(
                    n.get("data", {})
                    .get("kernel_revision", {})
                    .get("commit")
                    == firmware.git_commit_hash
                    for n in existing
                ):
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

# Include upload router
app.include_router(upload_router)

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
