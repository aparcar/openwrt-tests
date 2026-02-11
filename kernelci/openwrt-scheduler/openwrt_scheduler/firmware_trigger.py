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
from datetime import datetime, timezone

import httpx
import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
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
        snapshot_targets = config.get("snapshot_targets", [])
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
            # Snapshot branches get additional targets (e.g. malta/be)
            if branch.is_snapshot:
                targets = default_targets + snapshot_targets
            else:
                targets = default_targets

            source_config = {
                "enabled": True,
                "type": "openwrt_releases",
                "sources": {
                    branch.name: {
                        "url": branch.url,
                        "version": branch.version,
                        "branch": branch.name,
                        "check_interval": check_interval,
                        "targets": targets,
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

    async def get_open_jobs(self) -> list[dict]:
        """Get jobs waiting for a lab to claim."""
        return await self.api_client.query_nodes(
            kind="job", state="available", limit=100,
        )

    async def get_running_jobs(self) -> list[dict]:
        """Get jobs currently being executed."""
        return await self.api_client.query_nodes(
            kind="job", state="closing", limit=100,
        )

    async def get_recent_builds(self) -> list[dict]:
        """Get current firmware builds."""
        return await self.api_client.query_nodes(
            kind="kbuild", state="available", limit=50,
        )

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
# Status Dashboard
# =============================================================================


def _format_age(iso_timestamp: str) -> str:
    """Convert ISO timestamp to human-readable age."""
    try:
        ts = iso_timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        seconds = int(delta.total_seconds())
        if seconds < 0:
            return "just now"
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"
        return f"{seconds // 86400}d ago"
    except Exception:
        return iso_timestamp or "unknown"


def _h(text: str) -> str:
    """Escape HTML."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_dashboard(
    service: FirmwareTriggerService,
    open_jobs: list[dict],
    running_jobs: list[dict],
    builds: list[dict],
) -> str:
    """Render the status dashboard as HTML."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    config = service.config

    # --- Overview ---
    sources_info = []
    for s in service.sources:
        sources_info.append(
            f"<span class='tag'>{_h(s.name)}</span>"
        )
    sources_html = " ".join(sources_info) if sources_info else "<em>none</em>"

    enabled_types = config.get("scheduler", {}).get("enabled_test_types", [])
    types_html = " ".join(
        f"<span class='tag'>{_h(t)}</span>" for t in enabled_types
    ) or "<em>none</em>"

    # Build lookup: kbuild node id -> first artifact URL
    build_lookup: dict[str, str] = {}
    for b in builds:
        bid = b.get("id") or str(b.get("_id", ""))
        arts = b.get("data", {}).get("artifacts", {})
        if arts:
            build_lookup[bid] = next(iter(arts.values()))

    # --- Open Jobs table ---
    if open_jobs:
        open_rows = ""
        for job in open_jobs:
            d = job.get("data", {})
            kr = d.get("kernel_revision", {})
            parent = job.get("parent", "")
            fw_url = build_lookup.get(parent, "")
            fw_display = fw_url.split("/")[-1] if fw_url else ""
            fw_cell = f"<a href='{_h(fw_url)}'>{_h(fw_display)}</a>" if fw_url else ""
            open_rows += (
                f"<tr>"
                f"<td>{_h(d.get('device_type', ''))}</td>"
                f"<td>{_h(kr.get('branch', ''))}</td>"
                f"<td>{_h(d.get('test_type', ''))}</td>"
                f"<td>{fw_cell}</td>"
                f"<td>{_format_age(job.get('created', ''))}</td>"
                f"</tr>"
            )
        open_jobs_html = (
            f"<table><thead><tr>"
            f"<th>Device</th><th>Branch</th><th>Type</th>"
            f"<th>Firmware</th><th>Waiting</th>"
            f"</tr></thead><tbody>{open_rows}</tbody></table>"
        )
    else:
        open_jobs_html = "<p class='empty'>No open jobs</p>"

    # --- Running Jobs table ---
    if running_jobs:
        running_rows = ""
        for job in running_jobs:
            d = job.get("data", {})
            kr = d.get("kernel_revision", {})
            running_rows += (
                f"<tr>"
                f"<td>{_h(d.get('device_type', ''))}</td>"
                f"<td>{_h(kr.get('branch', ''))}</td>"
                f"<td>{_h(d.get('lab_name', ''))}</td>"
                f"<td>{_format_age(d.get('started_at', job.get('updated', '')))}</td>"
                f"</tr>"
            )
        running_jobs_html = (
            f"<table><thead><tr>"
            f"<th>Device</th><th>Branch</th><th>Lab</th><th>Started</th>"
            f"</tr></thead><tbody>{running_rows}</tbody></table>"
        )
    else:
        running_jobs_html = "<p class='empty'>No running jobs</p>"

    # --- Builds table ---
    if builds:
        build_rows = ""
        for b in builds:
            d = b.get("data", {})
            kr = d.get("kernel_revision", {})
            arts = d.get("artifacts", {})
            art_links = []
            for atype, url in arts.items():
                art_links.append(f"<a href='{_h(url)}'>{_h(atype)}</a>")
            build_rows += (
                f"<tr>"
                f"<td>{_h(d.get('target', ''))}/{_h(d.get('subtarget', ''))}</td>"
                f"<td>{_h(d.get('profile', ''))}</td>"
                f"<td>{_h(d.get('openwrt_version', ''))}</td>"
                f"<td>{_h(kr.get('branch', ''))}</td>"
                f"<td><code>{_h(kr.get('commit', '')[:10])}</code></td>"
                f"<td>{' '.join(art_links)}</td>"
                f"</tr>"
            )
        builds_html = (
            f"<table><thead><tr>"
            f"<th>Target</th><th>Profile</th><th>Version</th>"
            f"<th>Branch</th><th>Commit</th><th>Artifacts</th>"
            f"</tr></thead><tbody>{build_rows}</tbody></table>"
        )
    else:
        builds_html = "<p class='empty'>No builds</p>"

    # --- Device Types table ---
    device_types = config.get("device_types", {})
    if device_types:
        dev_rows = ""
        for name, dc in device_types.items():
            features = ", ".join(dc.get("features", []))
            caps = ", ".join(dc.get("capabilities", []))
            dev_rows += (
                f"<tr>"
                f"<td><strong>{_h(name)}</strong></td>"
                f"<td>{_h(dc.get('target', ''))}/{_h(dc.get('subtarget', ''))}</td>"
                f"<td>{_h(dc.get('profile', ''))}</td>"
                f"<td>{_h(features)}</td>"
                f"<td>{_h(caps)}</td>"
                f"</tr>"
            )
        devices_html = (
            f"<table><thead><tr>"
            f"<th>Name</th><th>Target</th><th>Profile</th>"
            f"<th>Features</th><th>Capabilities</th>"
            f"</tr></thead><tbody>{dev_rows}</tbody></table>"
        )
    else:
        devices_html = "<p class='empty'>No device types configured</p>"

    # --- Test Types table ---
    test_types = config.get("test_types", {})
    if test_types:
        type_rows = ""
        for name, tc in test_types.items():
            enabled = tc.get("enabled", True)
            status = "enabled" if enabled else "disabled"
            type_rows += (
                f"<tr>"
                f"<td><strong>{_h(name)}</strong></td>"
                f"<td>{_h(tc.get('description', ''))}</td>"
                f"<td><a href='{_h(tc.get('repository', ''))}'>{_h(tc.get('repository', '').split('/')[-1])}</a></td>"
                f"<td>{tc.get('timeout', '')}s</td>"
                f"<td>{_h(status)}</td>"
                f"</tr>"
            )
        types_table_html = (
            f"<table><thead><tr>"
            f"<th>Type</th><th>Description</th><th>Repository</th>"
            f"<th>Timeout</th><th>Status</th>"
            f"</tr></thead><tbody>{type_rows}</tbody></table>"
        )
    else:
        types_table_html = "<p class='empty'>No test types configured</p>"

    # --- Active Labs (inferred from running jobs) ---
    labs: dict[str, list[str]] = {}
    for job in running_jobs:
        d = job.get("data", {})
        lab = d.get("lab_name", "")
        if lab:
            labs.setdefault(lab, []).append(d.get("device_type", "unknown"))
    if labs:
        lab_rows = ""
        for lab_name, devices in labs.items():
            lab_rows += (
                f"<tr>"
                f"<td><strong>{_h(lab_name)}</strong></td>"
                f"<td>{len(devices)}</td>"
                f"<td>{_h(', '.join(sorted(set(devices))))}</td>"
                f"</tr>"
            )
        labs_html = (
            f"<table><thead><tr>"
            f"<th>Lab</th><th>Running Jobs</th><th>Devices</th>"
            f"</tr></thead><tbody>{lab_rows}</tbody></table>"
        )
    else:
        labs_html = "<p class='empty'>No labs active (no running jobs)</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>OpenWrt KernelCI Pipeline Status</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
         background: #0d1117; color: #c9d1d9; padding: 1.5rem; line-height: 1.5; }}
  h1 {{ color: #58a6ff; margin-bottom: 0.25rem; font-size: 1.4rem; }}
  h2 {{ color: #8b949e; font-size: 1.1rem; margin: 1.5rem 0 0.5rem; padding-bottom: 0.3rem;
        border-bottom: 1px solid #21262d; }}
  .subtitle {{ color: #8b949e; font-size: 0.85rem; margin-bottom: 1rem; }}
  .overview {{ display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 1rem 0; }}
  .stat {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px;
           padding: 0.75rem 1rem; min-width: 120px; }}
  .stat .label {{ color: #8b949e; font-size: 0.75rem; text-transform: uppercase; }}
  .stat .value {{ font-size: 1.5rem; font-weight: bold; color: #58a6ff; }}
  .stat .value.warn {{ color: #d29922; }}
  .stat .value.ok {{ color: #3fb950; }}
  .tag {{ background: #21262d; border: 1px solid #30363d; border-radius: 3px;
          padding: 0.15rem 0.5rem; font-size: 0.8rem; display: inline-block; margin: 0.1rem; }}
  table {{ width: 100%; border-collapse: collapse; margin: 0.5rem 0; font-size: 0.85rem; }}
  th {{ background: #161b22; color: #8b949e; text-align: left; padding: 0.5rem 0.75rem;
       font-weight: 600; font-size: 0.75rem; text-transform: uppercase; }}
  td {{ padding: 0.4rem 0.75rem; border-bottom: 1px solid #21262d; }}
  tr:hover {{ background: #161b22; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  code {{ background: #161b22; padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.8rem; }}
  .empty {{ color: #484f58; font-style: italic; padding: 0.5rem 0; }}
  .footer {{ margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #21262d;
             color: #484f58; font-size: 0.75rem; }}
</style>
</head>
<body>
<h1>OpenWrt KernelCI Pipeline Status</h1>
<div class="subtitle">Auto-refreshes every 30 seconds &middot; Generated {now}</div>

<div class="overview">
  <div class="stat">
    <div class="label">Sources</div>
    <div class="value">{len(service.sources)}</div>
  </div>
  <div class="stat">
    <div class="label">Open Jobs</div>
    <div class="value{' warn' if open_jobs else ' ok'}">{len(open_jobs)}</div>
  </div>
  <div class="stat">
    <div class="label">Running</div>
    <div class="value{' ok' if running_jobs else ''}">{len(running_jobs)}</div>
  </div>
  <div class="stat">
    <div class="label">Builds</div>
    <div class="value">{len(builds)}</div>
  </div>
</div>

<div>
  <strong>Sources:</strong> {sources_html}
  &nbsp;&nbsp;
  <strong>Test types:</strong> {types_html}
</div>

<h2>Open Jobs ({len(open_jobs)} waiting for labs)</h2>
{open_jobs_html}

<h2>Running Jobs ({len(running_jobs)} in progress)</h2>
{running_jobs_html}

<h2>Firmware Builds ({len(builds)})</h2>
{builds_html}

<h2>Device Types ({len(device_types)})</h2>
{devices_html}

<h2>Test Types ({len(test_types)})</h2>
{types_table_html}

<h2>Active Labs</h2>
{labs_html}

<div class="footer">
  OpenWrt KernelCI Pipeline &middot;
  <a href="/health">Health</a> &middot;
  <a href="/sources">Sources API</a> &middot;
  <a href="/status/jobs">Jobs JSON</a> &middot;
  <a href="/status/builds">Builds JSON</a> &middot;
  <a href="/status/config">Config JSON</a> &middot;
  <a href="/docs">API Docs</a>
</div>
</body>
</html>"""


@app.get("/", include_in_schema=False)
async def root():
    """Redirect to status dashboard."""
    return RedirectResponse(url="/status")


@app.get("/status", response_class=HTMLResponse)
async def status_dashboard():
    """Pipeline status dashboard."""
    if not _service or not _service.api_client:
        return HTMLResponse("<h1>Service not initialized</h1>", status_code=503)

    try:
        open_jobs, running_jobs, builds = await asyncio.gather(
            _service.get_open_jobs(),
            _service.get_running_jobs(),
            _service.get_recent_builds(),
        )
    except Exception as e:
        return HTMLResponse(
            f"<h1>Error fetching data</h1><pre>{_h(str(e))}</pre>",
            status_code=500,
        )

    return HTMLResponse(
        content=_render_dashboard(_service, open_jobs, running_jobs, builds)
    )


@app.get("/status/jobs")
async def status_jobs():
    """Open and running jobs as JSON."""
    if not _service or not _service.api_client:
        return {"error": "not initialized"}
    open_jobs, running_jobs = await asyncio.gather(
        _service.get_open_jobs(),
        _service.get_running_jobs(),
    )
    return {"open": open_jobs, "running": running_jobs}


@app.get("/status/builds")
async def status_builds():
    """Recent firmware builds as JSON."""
    if not _service or not _service.api_client:
        return {"error": "not initialized"}
    return {"builds": await _service.get_recent_builds()}


@app.get("/status/config")
async def status_config():
    """Pipeline configuration as JSON."""
    if not _service:
        return {"error": "not initialized"}
    config = _service.config
    return {
        "device_types": config.get("device_types", {}),
        "test_types": config.get("test_types", {}),
        "scheduler": config.get("scheduler", {}),
        "firmware_sources": {
            s.name: {
                "enabled": s.is_enabled(),
                "check_interval": s.get_check_interval(),
            }
            for s in _service.sources
        },
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
