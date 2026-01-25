# Self-Hosted KernelCI for OpenWrt Testing

## Executive Summary

This document outlines how to deploy a **self-hosted KernelCI instance** for OpenWrt firmware testing. The system will provide:

- **Test result visualization** via the KernelCI dashboard
- **Job scheduling** with labgrid runtime support
- **Multi-source firmware management** (official releases, PRs, custom builds)
- **Device health checks** with automated monitoring
- **Decentralized lab federation** using pull-mode architecture

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Self-Hosted KernelCI Components](#self-hosted-kernelci-components)
3. [OpenWrt-Specific Adaptations](#openwrt-specific-adaptations)
4. [Firmware Source Management](#firmware-source-management)
5. [Health Check System](#health-check-system)
6. [Deployment Guide](#deployment-guide)
7. [Implementation Plan](#implementation-plan)

---

## Architecture Overview

### High-Level System Design

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                      Self-Hosted OpenWrt KernelCI Instance                           │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                         Firmware Sources                                      │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │   │
│  │  │   Official   │  │   GitHub     │  │   Custom     │  │  Buildbot    │    │   │
│  │  │   Releases   │  │   PR Builds  │  │   Builds     │  │  Integration │    │   │
│  │  │  (snapshot,  │  │  (CI arti-   │  │  (developer  │  │  (upstream   │    │   │
│  │  │   stable)    │  │   facts)     │  │   uploads)   │  │   builds)    │    │   │
│  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │   │
│  └─────────┼─────────────────┼─────────────────┼─────────────────┼────────────┘   │
│            └─────────────────┴────────┬────────┴─────────────────┘                │
│                                       ▼                                            │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                    KernelCI API (Maestro) - Self-Hosted                      │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐    │   │
│  │  │  FastAPI    │  │  MongoDB    │  │   Redis     │  │  Artifact       │    │   │
│  │  │  REST API   │  │  Database   │  │  Pub/Sub    │  │  Storage (S3)   │    │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘    │   │
│  └──────────────────────────────────┬──────────────────────────────────────────┘   │
│                                     │                                               │
│                          Events (new firmware, test triggers)                       │
│                                     │                                               │
│  ┌──────────────────────────────────┼──────────────────────────────────────────┐   │
│  │            KernelCI Pipeline - OpenWrt Scheduler                             │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐    │   │
│  │  │  Firmware   │  │   Test      │  │   Health    │  │  Results        │    │   │
│  │  │  Trigger    │  │  Scheduler  │  │   Check     │  │  Collector      │    │   │
│  │  │  (watches   │  │  (assigns   │  │  Scheduler  │  │  (aggregates)   │    │   │
│  │  │   sources)  │  │   to labs)  │  │  (periodic) │  │                 │    │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘    │   │
│  └──────────────────────────────────┬──────────────────────────────────────────┘   │
│                                     │                                               │
│                          Pull-mode job distribution                                 │
│                                     │                                               │
│  ┌──────────────────────────────────┼──────────────────────────────────────────┐   │
│  │                    Labgrid Adapter (per lab)                                 │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐    │   │
│  │  │ Job Poller  │  │  pytest     │  │  Firmware   │  │  Health Check   │    │   │
│  │  │ (pull-mode) │  │  Executor   │  │  Flasher    │  │  Runner         │    │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘    │   │
│  └──────────────────────────────────┬──────────────────────────────────────────┘   │
│                                     │                                               │
│            ┌────────────────────────┼────────────────────────┐                     │
│            ▼                        ▼                        ▼                     │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐             │
│  │  Lab: aparcar    │    │  Lab: leinelab   │    │  Lab: community  │             │
│  │  (coordinator)   │    │  (exporter)      │    │  (pull-mode)     │             │
│  │  ┌────────────┐  │    │  ┌────────────┐  │    │  ┌────────────┐  │             │
│  │  │ OpenWrt One│  │    │  │ BPi R4     │  │    │  │ Custom HW  │  │             │
│  │  │ Linksys    │  │    │  │ RPi 4      │  │    │  │ QEMU       │  │             │
│  │  │ TP-Link    │  │    │  │ GL.iNet    │  │    │  │            │  │             │
│  │  └────────────┘  │    │  └────────────┘  │    │  └────────────┘  │             │
│  └──────────────────┘    └──────────────────┘    └──────────────────┘             │
│                                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                         Dashboard (Web UI)                                    │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │   │
│  │  │ Device Fleet    │  │ Test Results    │  │ Firmware Comparison         │  │   │
│  │  │ Status          │  │ Matrix          │  │ (version × device × test)   │  │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │   │
│  │  │ Health Check    │  │ Regression      │  │ PR Status                   │  │   │
│  │  │ Dashboard       │  │ Tracking        │  │ (test before merge)         │  │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                      │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Self-Hosted**: Complete control over infrastructure, no external dependencies
2. **Firmware-Centric**: Designed around OpenWrt images, not kernel builds
3. **Multi-Source**: Accept firmware from official, PR, custom, and buildbot sources
4. **Health-First**: Device health monitoring as a core feature
5. **Decentralized**: Labs operate independently, pull jobs when ready

---

## Self-Hosted KernelCI Components

### Component Stack

| Component | Technology | Purpose | Port |
|-----------|------------|---------|------|
| **API Server** | kernelci-api (FastAPI) | REST API, job management | 8001 |
| **Database** | MongoDB | Store nodes, jobs, results | 27017 |
| **Message Queue** | Redis | Pub/Sub for events | 6379 |
| **Pipeline** | kernelci-pipeline | Job scheduling, triggers | - |
| **Dashboard** | kernelci-dashboard (React) | Web visualization | 3000 |
| **Storage** | MinIO (S3-compatible) | Artifacts, firmware, logs | 9000 |
| **Reverse Proxy** | Traefik/nginx | TLS termination, routing | 443 |

### Modified Components for OpenWrt

KernelCI is designed for Linux kernel testing. For OpenWrt, we need these adaptations:

| KernelCI Concept | OpenWrt Adaptation |
|------------------|-------------------|
| Kernel build | Firmware image (sysupgrade, factory, initramfs) |
| Kernel tree | OpenWrt repository + target/subtarget |
| Defconfig | Device profile |
| Boot test | Flash + boot + shell access |
| kunit/kselftest | pytest test suite |

---

## OpenWrt-Specific Adaptations

### Node Schema for OpenWrt Firmware

```python
# openwrt_schema.py
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class OpenWrtFirmware(BaseModel):
    """Schema for OpenWrt firmware in KernelCI."""

    # Core identification
    id: str                          # "openwrt:snapshot:ath79-generic:tplink_archer-c7-v2:20250124"
    origin: str = "openwrt"

    # Firmware source
    source: str                      # "official", "pr", "custom", "buildbot"
    source_url: Optional[str]        # Download URL or PR reference

    # OpenWrt-specific fields (replaces kernel fields)
    version: str                     # "SNAPSHOT", "24.10.0", "23.05.5"
    target: str                      # "ath79"
    subtarget: str                   # "generic"
    profile: str                     # "tplink_archer-c7-v2"

    # Git information
    git_repository_url: str          # "https://github.com/openwrt/openwrt"
    git_commit_hash: str             # commit SHA
    git_branch: Optional[str]        # "main", "openwrt-24.10"

    # Build artifacts
    artifacts: dict                  # URLs to firmware files
    # Example:
    # {
    #   "sysupgrade": "https://.../openwrt-ath79-generic-tplink_archer-c7-v2-squashfs-sysupgrade.bin",
    #   "factory": "https://.../openwrt-ath79-generic-tplink_archer-c7-v2-squashfs-factory.bin",
    #   "initramfs": "https://.../openwrt-ath79-generic-tplink_archer-c7-v2-initramfs-kernel.bin",
    #   "manifest": "https://.../openwrt-ath79-generic-tplink_archer-c7-v2.manifest"
    # }

    # Metadata
    build_time: datetime
    file_size: int
    sha256: str

    # Features (from profiles.json)
    features: List[str]              # ["wifi", "usb", "poe"]
    packages: List[str]              # installed packages


class OpenWrtTestJob(BaseModel):
    """Schema for OpenWrt test jobs."""

    id: str
    firmware_id: str                 # Reference to OpenWrtFirmware
    device_type: str                 # Labgrid target name
    test_plan: List[str]             # ["test_shell", "test_ssh", "test_wifi_wpa3"]
    required_features: List[str]     # Device must have these features
    priority: int = 5                # 1-10, higher = more urgent
    timeout: int = 1800              # seconds

    # Job state
    status: str                      # "pending", "running", "complete", "failed"
    assigned_lab: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


class OpenWrtTestResult(BaseModel):
    """Schema for OpenWrt test results."""

    id: str
    job_id: str
    firmware_id: str
    device_type: str
    lab_name: str

    # Test execution
    test_name: str
    status: str                      # "pass", "fail", "skip", "error"
    duration: float                  # seconds
    start_time: datetime

    # Output
    log_url: Optional[str]
    console_log_url: Optional[str]
    error_message: Optional[str]

    # Environment
    environment: dict
    # {
    #   "device_serial": "...",
    #   "firmware_version": "...",
    #   "kernel_version": "...",
    #   "lab_name": "...",
    # }
```

### Pipeline Configuration for OpenWrt

```yaml
# config/pipeline-openwrt.yaml
api:
  openwrt-local:
    url: http://localhost:8001
    token: ${OPENWRT_KCI_TOKEN}

storage:
  openwrt-minio:
    storage_type: s3
    base_url: http://minio:9000
    bucket: openwrt-artifacts
    access_key: ${MINIO_ACCESS_KEY}
    secret_key: ${MINIO_SECRET_KEY}

# Firmware source watchers
triggers:
  # Watch official OpenWrt releases
  openwrt-official:
    type: firmware_watcher
    sources:
      - name: snapshot
        url: https://downloads.openwrt.org/snapshots/targets/
        interval: 3600  # Check hourly
        pattern: "*/*/profiles.json"

      - name: stable
        url: https://downloads.openwrt.org/releases/24.10.0/targets/
        interval: 86400  # Check daily

      - name: oldstable
        url: https://downloads.openwrt.org/releases/23.05.5/targets/
        interval: 86400

  # Watch GitHub PR artifacts
  openwrt-github-pr:
    type: github_artifacts
    repository: openwrt/openwrt
    workflow: "build.yml"
    artifact_pattern: "openwrt-*"
    on_labels: ["ci-test-requested"]

  # Accept custom uploads via API
  openwrt-custom:
    type: api_upload
    endpoint: /api/v1/firmware/upload
    validation:
      required_fields: ["target", "subtarget", "profile"]
      max_size: 100MB

# Runtime definitions
runtimes:
  labgrid:
    type: labgrid
    adapter: labgrid-kci-adapter
    # Labs pull jobs, we don't push

# Test plan definitions
test_plans:
  openwrt-base:
    description: "Basic boot and connectivity tests"
    tests:
      - test_shell
      - test_ssh
      - test_firmware_version
      - test_ubus_system_board
      - test_free_memory
      - test_kernel_errors
    timeout: 600
    required_features: []

  openwrt-system:
    description: "System health validation"
    tests:
      - test_memory_usage
      - test_filesystem_usage
      - test_system_uptime
      - test_process_count
      - test_entropy_available
    timeout: 300
    required_features: []

  openwrt-network:
    description: "Network functionality tests"
    tests:
      - test_lan_interface_address
      - test_wan_wait_for_network
      - test_https_download
    timeout: 600
    required_features: ["wan_port", "online"]

  openwrt-wifi:
    description: "WiFi functionality tests"
    tests:
      - test_wifi_scan
      - test_wifi_wpa2
      - test_wifi_wpa3
    timeout: 900
    required_features: ["wifi"]

  openwrt-package:
    description: "Package manager tests"
    tests:
      - test_opkg_procd_installed
      - test_opkg_install_ucert
    timeout: 300
    required_features: ["opkg", "online"]

# Scheduler configuration
scheduler:
  # Map device types to test plans
  device_test_mapping:
    # All devices get base tests
    default:
      - openwrt-base
      - openwrt-system

    # Devices with specific features get additional tests
    feature_wifi:
      - openwrt-wifi

    feature_wan_port:
      - openwrt-network

    feature_opkg:
      - openwrt-package

  # Priority rules
  priorities:
    pr_builds: 10        # Highest - developers waiting
    snapshot: 5          # Medium - daily testing
    stable: 3            # Lower - release validation
    custom: 7            # Developer uploads

  # Health check scheduling
  health_checks:
    enabled: true
    interval: 86400      # Daily
    tests: ["test_shell", "test_ssh"]
    on_failure:
      notify: ["email", "github_issue"]
      disable_device: true
```

---

## Firmware Source Management

### Source Types

#### 1. Official OpenWrt Releases

```python
# firmware_sources/official.py
import httpx
from datetime import datetime

class OfficialFirmwareSource:
    """Watch official OpenWrt download server for new firmware."""

    BASE_URLS = {
        "snapshot": "https://downloads.openwrt.org/snapshots/targets",
        "stable": "https://downloads.openwrt.org/releases/24.10.0/targets",
        "oldstable": "https://downloads.openwrt.org/releases/23.05.5/targets",
    }

    async def scan_for_firmware(self, version: str) -> list[dict]:
        """Scan for available firmware images."""
        base_url = self.BASE_URLS[version]
        firmware_list = []

        # Get list of targets
        async with httpx.AsyncClient() as client:
            # Parse target directories
            targets = await self._list_targets(client, base_url)

            for target, subtarget in targets:
                profiles_url = f"{base_url}/{target}/{subtarget}/profiles.json"
                try:
                    resp = await client.get(profiles_url)
                    profiles = resp.json()

                    for profile_name, profile_data in profiles["profiles"].items():
                        firmware_list.append({
                            "source": "official",
                            "version": version,
                            "target": target,
                            "subtarget": subtarget,
                            "profile": profile_name,
                            "artifacts": self._build_artifact_urls(
                                base_url, target, subtarget, profile_data
                            ),
                            "features": profile_data.get("device_packages", []),
                        })
                except Exception as e:
                    logger.warning(f"Failed to fetch profiles for {target}/{subtarget}: {e}")

        return firmware_list

    def _build_artifact_urls(self, base_url, target, subtarget, profile) -> dict:
        """Build URLs for firmware artifacts."""
        images = profile.get("images", [])
        artifacts = {}

        for image in images:
            image_type = image.get("type", "unknown")
            filename = image.get("name")
            if filename:
                artifacts[image_type] = f"{base_url}/{target}/{subtarget}/{filename}"
                artifacts[f"{image_type}_sha256"] = image.get("sha256")

        return artifacts
```

#### 2. GitHub PR Builds

```python
# firmware_sources/github_pr.py
import httpx
from github import Github

class GitHubPRFirmwareSource:
    """Fetch firmware from GitHub Actions artifacts for PRs."""

    def __init__(self, token: str, repo: str = "openwrt/openwrt"):
        self.gh = Github(token)
        self.repo = self.gh.get_repo(repo)

    async def get_pr_firmware(self, pr_number: int) -> list[dict]:
        """Get firmware artifacts from a PR's CI run."""
        pr = self.repo.get_pull(pr_number)

        # Find the latest successful workflow run
        runs = self.repo.get_workflow_runs(
            branch=pr.head.ref,
            status="success"
        )

        if runs.totalCount == 0:
            return []

        latest_run = runs[0]
        artifacts = latest_run.get_artifacts()

        firmware_list = []
        for artifact in artifacts:
            if artifact.name.startswith("openwrt-"):
                # Parse target info from artifact name
                # e.g., "openwrt-ath79-generic"
                parts = artifact.name.split("-")
                if len(parts) >= 3:
                    firmware_list.append({
                        "source": "pr",
                        "source_ref": f"PR #{pr_number}",
                        "version": f"pr-{pr_number}",
                        "target": parts[1],
                        "subtarget": parts[2],
                        "git_commit_hash": pr.head.sha,
                        "git_branch": pr.head.ref,
                        "artifact_id": artifact.id,
                        "artifact_url": artifact.archive_download_url,
                    })

        return firmware_list

    async def download_and_extract(self, artifact_id: int, dest_path: str):
        """Download and extract PR artifact to storage."""
        artifact = self.repo.get_workflow_run_artifact(artifact_id)
        # Download zip and extract firmware files
        # Upload to MinIO storage
        pass
```

#### 3. Custom/Developer Uploads

```python
# firmware_sources/custom_upload.py
from fastapi import APIRouter, UploadFile, File, Form
from pydantic import BaseModel
import hashlib

router = APIRouter()

class FirmwareUploadResponse(BaseModel):
    firmware_id: str
    status: str
    message: str

@router.post("/api/v1/firmware/upload")
async def upload_custom_firmware(
    firmware_file: UploadFile = File(...),
    target: str = Form(...),
    subtarget: str = Form(...),
    profile: str = Form(...),
    version: str = Form(default="custom"),
    git_commit: str = Form(default=None),
    description: str = Form(default=None),
) -> FirmwareUploadResponse:
    """
    Upload custom firmware for testing.

    Allows developers to upload their own builds for testing
    on the shared infrastructure.
    """
    # Validate file
    content = await firmware_file.read()
    sha256 = hashlib.sha256(content).hexdigest()

    # Store in MinIO
    storage_path = f"custom/{target}/{subtarget}/{profile}/{sha256[:8]}/{firmware_file.filename}"
    await storage.upload(storage_path, content)

    # Create firmware node
    firmware_id = f"openwrt:custom:{target}-{subtarget}:{profile}:{sha256[:12]}"

    firmware_node = {
        "id": firmware_id,
        "source": "custom",
        "version": version,
        "target": target,
        "subtarget": subtarget,
        "profile": profile,
        "artifacts": {
            "sysupgrade": storage.get_url(storage_path),
        },
        "sha256": sha256,
        "description": description,
        "git_commit_hash": git_commit,
    }

    # Submit to API
    await api.submit_node(firmware_node)

    # Trigger test jobs
    await scheduler.create_jobs_for_firmware(firmware_id)

    return FirmwareUploadResponse(
        firmware_id=firmware_id,
        status="accepted",
        message=f"Firmware uploaded. Test jobs queued."
    )
```

#### 4. Buildbot Integration

```python
# firmware_sources/buildbot.py
class BuildbotFirmwareSource:
    """
    Integration with OpenWrt Buildbot.

    Listens for build completion webhooks from Buildbot
    and imports firmware for testing.
    """

    def __init__(self, buildbot_url: str, api_key: str):
        self.buildbot_url = buildbot_url
        self.api_key = api_key

    async def handle_build_complete(self, build_data: dict):
        """Handle Buildbot build completion webhook."""
        if build_data["results"] != "success":
            return

        builder = build_data["builder"]
        # Parse target from builder name, e.g., "target/ath79/generic"
        target, subtarget = self._parse_builder_name(builder)

        # Get artifact URLs from Buildbot
        artifacts = await self._fetch_build_artifacts(build_data["buildid"])

        # Create firmware nodes for each profile
        for profile, artifact_url in artifacts.items():
            firmware_node = {
                "source": "buildbot",
                "version": "buildbot",
                "target": target,
                "subtarget": subtarget,
                "profile": profile,
                "artifacts": {"sysupgrade": artifact_url},
                "git_commit_hash": build_data.get("revision"),
            }
            await api.submit_node(firmware_node)
```

---

## Health Check System

### Health Check Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Health Check System                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Health Check Scheduler                            │   │
│  │                                                                       │   │
│  │  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────────┐   │   │
│  │  │  Periodic   │   │  On-Demand  │   │  Post-Failure           │   │   │
│  │  │  (daily)    │   │  (API call) │   │  (verify fix)           │   │   │
│  │  └──────┬──────┘   └──────┬──────┘   └───────────┬─────────────┘   │   │
│  │         └─────────────────┴──────────────────────┘                   │   │
│  │                           │                                           │   │
│  │                           ▼                                           │   │
│  │  ┌─────────────────────────────────────────────────────────────┐    │   │
│  │  │              Health Check Job Generator                      │    │   │
│  │  │  - Creates minimal test jobs (test_shell, test_ssh)          │    │   │
│  │  │  - High priority for quick execution                         │    │   │
│  │  │  - No firmware flash (use existing)                          │    │   │
│  │  └─────────────────────────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                     │                                       │
│                                     ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Health Check Executor                             │   │
│  │                                                                       │   │
│  │  For each device:                                                     │   │
│  │  1. Power on device (PDUDaemon)                                       │   │
│  │  2. Wait for boot (serial console)                                    │   │
│  │  3. Verify shell access                                               │   │
│  │  4. Verify SSH access                                                 │   │
│  │  5. Run basic diagnostics                                             │   │
│  │  6. Record results                                                    │   │
│  │  7. Power off device                                                  │   │
│  └──────────────────────────────────┬──────────────────────────────────┘   │
│                                     │                                       │
│                                     ▼                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Health Status Manager                             │   │
│  │                                                                       │   │
│  │  ┌─────────────────────────────────────────────────────────────┐    │   │
│  │  │                    Device Registry                           │    │   │
│  │  │  device_id  │ status  │ last_check │ last_pass │ failures   │    │   │
│  │  │  ─────────────────────────────────────────────────────────── │    │   │
│  │  │  openwrt-one│ healthy │ 2025-01-24 │ 2025-01-24│ 0          │    │   │
│  │  │  bpi-r4     │ healthy │ 2025-01-24 │ 2025-01-24│ 0          │    │   │
│  │  │  linksys    │ failing │ 2025-01-24 │ 2025-01-20│ 4          │    │   │
│  │  │  tplink-wr  │ disabled│ 2025-01-15 │ 2025-01-10│ 5 (max)    │    │   │
│  │  └─────────────────────────────────────────────────────────────┘    │   │
│  │                                                                       │   │
│  │  Actions on failure:                                                  │   │
│  │  - consecutive_failures >= 3: Mark device as "failing"               │   │
│  │  - consecutive_failures >= 5: Disable device, open issue             │   │
│  │  - After manual fix: Verify with on-demand health check              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Notification System                               │   │
│  │                                                                       │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐    │   │
│  │  │ GitHub Issue │  │ Email/Slack  │  │ Dashboard Alert        │    │   │
│  │  │ (auto-create │  │ Notification │  │ (visual indicator)     │    │   │
│  │  │  & close)    │  │              │  │                        │    │   │
│  │  └──────────────┘  └──────────────┘  └────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Health Check Implementation

```python
# health/scheduler.py
from datetime import datetime, timedelta
from typing import Optional
import asyncio

class HealthCheckScheduler:
    """Schedule and manage device health checks."""

    def __init__(self, api_client, device_registry):
        self.api = api_client
        self.devices = device_registry
        self.check_interval = timedelta(hours=24)
        self.failure_threshold = 5

    async def run_periodic_checks(self):
        """Main loop for periodic health checks."""
        while True:
            devices_to_check = self.devices.get_devices_needing_check(
                interval=self.check_interval
            )

            for device in devices_to_check:
                await self.schedule_health_check(device)

            await asyncio.sleep(3600)  # Check hourly for devices due

    async def schedule_health_check(self, device: dict):
        """Create health check job for a device."""
        job = {
            "type": "health_check",
            "device_type": device["name"],
            "test_plan": ["test_shell", "test_ssh"],
            "priority": 10,  # Highest priority
            "timeout": 300,  # 5 minutes max
            "skip_firmware_flash": True,  # Use existing firmware
        }

        await self.api.create_job(job)

    async def process_health_result(self, result: dict):
        """Process health check result and update device status."""
        device_id = result["device_type"]
        passed = result["status"] == "pass"

        if passed:
            await self.devices.mark_healthy(device_id)
            await self._close_failure_issue(device_id)
        else:
            failures = await self.devices.increment_failures(device_id)

            if failures >= self.failure_threshold:
                await self.devices.disable_device(device_id)
                await self._create_failure_issue(device_id, result)
            elif failures >= 3:
                await self.devices.mark_failing(device_id)
                await self._notify_failure(device_id, result)

    async def _create_failure_issue(self, device_id: str, result: dict):
        """Create GitHub issue for persistent device failure."""
        issue_body = f"""
## Device Health Check Failure

**Device:** {device_id}
**Last Check:** {datetime.now().isoformat()}
**Consecutive Failures:** {self.failure_threshold}

### Error Details
```
{result.get('error_message', 'No error message')}
```

### Console Log
{result.get('console_log_url', 'No console log available')}

### Actions Taken
- Device has been **disabled** from the test pool
- No new test jobs will be scheduled for this device

### Resolution
1. Investigate the device manually
2. Fix any hardware/network issues
3. Run manual health check: `POST /api/v1/health-check/{device_id}`
4. Device will be re-enabled after successful health check

/label ~"device-failure" ~"health-check"
"""
        await self.github.create_issue(
            title=f"[Health Check] {device_id} failing - disabled",
            body=issue_body,
            labels=["device-failure", "health-check"]
        )


# health/executor.py
class HealthCheckExecutor:
    """Execute health checks on labgrid devices."""

    async def run_health_check(self, device_name: str) -> dict:
        """Run health check on a specific device."""
        start_time = datetime.now()
        results = {
            "device_type": device_name,
            "start_time": start_time,
            "checks": [],
        }

        try:
            # Acquire device
            target = await self.labgrid.acquire_target(device_name)

            # Power cycle if supported
            if hasattr(target, 'power'):
                await target.power.cycle()
                await asyncio.sleep(5)

            # Check 1: Serial/Shell access
            shell_result = await self._check_shell(target)
            results["checks"].append(shell_result)

            if not shell_result["passed"]:
                results["status"] = "fail"
                results["error_message"] = "Shell access failed"
                return results

            # Check 2: SSH access
            ssh_result = await self._check_ssh(target)
            results["checks"].append(ssh_result)

            if not ssh_result["passed"]:
                results["status"] = "fail"
                results["error_message"] = "SSH access failed"
                return results

            # Check 3: Basic system health
            system_result = await self._check_system(target)
            results["checks"].append(system_result)

            # All checks passed
            results["status"] = "pass"
            results["duration"] = (datetime.now() - start_time).total_seconds()

        except Exception as e:
            results["status"] = "error"
            results["error_message"] = str(e)

        finally:
            await self.labgrid.release_target(device_name)

        return results

    async def _check_shell(self, target) -> dict:
        """Verify shell access works."""
        try:
            shell = target.get_driver("ShellDriver")
            output = shell.run_check("echo health_check_ok")
            return {
                "name": "shell_access",
                "passed": "health_check_ok" in output,
                "output": output,
            }
        except Exception as e:
            return {
                "name": "shell_access",
                "passed": False,
                "error": str(e),
            }

    async def _check_ssh(self, target) -> dict:
        """Verify SSH access works."""
        try:
            ssh = target.get_driver("SSHDriver")
            output = ssh.run_check("uname -a")
            return {
                "name": "ssh_access",
                "passed": "Linux" in output,
                "output": output,
            }
        except Exception as e:
            return {
                "name": "ssh_access",
                "passed": False,
                "error": str(e),
            }

    async def _check_system(self, target) -> dict:
        """Run basic system health checks."""
        ssh = target.get_driver("SSHDriver")
        checks = []

        # Memory check
        mem_output = ssh.run_check("free -m | grep Mem")
        mem_available = int(mem_output.split()[6])
        checks.append({
            "name": "memory",
            "passed": mem_available > 10,
            "value": mem_available,
            "unit": "MB",
        })

        # Disk check
        disk_output = ssh.run_check("df / | tail -1")
        disk_usage = int(disk_output.split()[4].rstrip('%'))
        checks.append({
            "name": "disk",
            "passed": disk_usage < 95,
            "value": disk_usage,
            "unit": "%",
        })

        return {
            "name": "system_health",
            "passed": all(c["passed"] for c in checks),
            "checks": checks,
        }
```

### Health Check Dashboard View

```typescript
// dashboard/components/HealthCheckView.tsx
interface DeviceHealth {
  device_id: string;
  status: 'healthy' | 'failing' | 'disabled' | 'unknown';
  last_check: string;
  last_pass: string;
  consecutive_failures: number;
  lab_name: string;
  features: string[];
}

const HealthCheckDashboard: React.FC = () => {
  const [devices, setDevices] = useState<DeviceHealth[]>([]);

  return (
    <div className="health-dashboard">
      <h2>Device Fleet Health Status</h2>

      <div className="health-summary">
        <StatCard
          title="Healthy"
          count={devices.filter(d => d.status === 'healthy').length}
          color="green"
        />
        <StatCard
          title="Failing"
          count={devices.filter(d => d.status === 'failing').length}
          color="yellow"
        />
        <StatCard
          title="Disabled"
          count={devices.filter(d => d.status === 'disabled').length}
          color="red"
        />
      </div>

      <table className="device-table">
        <thead>
          <tr>
            <th>Device</th>
            <th>Lab</th>
            <th>Status</th>
            <th>Last Check</th>
            <th>Last Pass</th>
            <th>Failures</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {devices.map(device => (
            <tr key={device.device_id} className={`status-${device.status}`}>
              <td>{device.device_id}</td>
              <td>{device.lab_name}</td>
              <td><StatusBadge status={device.status} /></td>
              <td>{formatDate(device.last_check)}</td>
              <td>{formatDate(device.last_pass)}</td>
              <td>{device.consecutive_failures}</td>
              <td>
                <button onClick={() => triggerHealthCheck(device.device_id)}>
                  Check Now
                </button>
                {device.status === 'disabled' && (
                  <button onClick={() => enableDevice(device.device_id)}>
                    Re-enable
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
```

---

## Deployment Guide

### Docker Compose Stack

```yaml
# docker-compose.yml
version: '3.8'

services:
  # ============================================
  # Core Infrastructure
  # ============================================

  mongodb:
    image: mongo:7.0
    container_name: openwrt-kci-mongodb
    volumes:
      - mongodb_data:/data/db
    environment:
      MONGO_INITDB_ROOT_USERNAME: ${MONGO_USER:-admin}
      MONGO_INITDB_ROOT_PASSWORD: ${MONGO_PASSWORD}
    networks:
      - kci-network
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    container_name: openwrt-kci-redis
    volumes:
      - redis_data:/data
    networks:
      - kci-network
    restart: unless-stopped

  minio:
    image: minio/minio:latest
    container_name: openwrt-kci-minio
    command: server /data --console-address ":9001"
    volumes:
      - minio_data:/data
    environment:
      MINIO_ROOT_USER: ${MINIO_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY}
    ports:
      - "9000:9000"
      - "9001:9001"
    networks:
      - kci-network
    restart: unless-stopped

  # ============================================
  # KernelCI API (Maestro)
  # ============================================

  kernelci-api:
    image: ghcr.io/kernelci/kernelci-api:latest
    container_name: openwrt-kci-api
    depends_on:
      - mongodb
      - redis
    environment:
      SECRET_KEY: ${KCI_SECRET_KEY}
      MONGO_SERVICE: mongodb://mongodb:27017
      REDIS_HOST: redis
      ALGORITHM: HS256
      ACCESS_TOKEN_EXPIRE_MINUTES: 480
    volumes:
      - ./config/api-config.toml:/home/kernelci/config/kernelci.toml:ro
    ports:
      - "8001:8001"
    networks:
      - kci-network
    restart: unless-stopped

  # ============================================
  # KernelCI Pipeline Services
  # ============================================

  pipeline-trigger:
    image: ghcr.io/kernelci/kernelci-pipeline:latest
    container_name: openwrt-kci-trigger
    depends_on:
      - kernelci-api
    environment:
      KCI_API_URL: http://kernelci-api:8001
      KCI_API_TOKEN: ${KCI_API_TOKEN}
    volumes:
      - ./config/pipeline-openwrt.yaml:/home/kernelci/config/pipeline.yaml:ro
      - ./openwrt-pipeline:/home/kernelci/openwrt-pipeline:ro
    command: ["python", "-m", "openwrt_pipeline.firmware_trigger"]
    networks:
      - kci-network
    restart: unless-stopped

  pipeline-scheduler:
    image: ghcr.io/kernelci/kernelci-pipeline:latest
    container_name: openwrt-kci-scheduler
    depends_on:
      - kernelci-api
    environment:
      KCI_API_URL: http://kernelci-api:8001
      KCI_API_TOKEN: ${KCI_API_TOKEN}
    volumes:
      - ./config/pipeline-openwrt.yaml:/home/kernelci/config/pipeline.yaml:ro
      - ./openwrt-pipeline:/home/kernelci/openwrt-pipeline:ro
    command: ["python", "-m", "openwrt_pipeline.test_scheduler"]
    networks:
      - kci-network
    restart: unless-stopped

  pipeline-health:
    image: ghcr.io/kernelci/kernelci-pipeline:latest
    container_name: openwrt-kci-health
    depends_on:
      - kernelci-api
    environment:
      KCI_API_URL: http://kernelci-api:8001
      KCI_API_TOKEN: ${KCI_API_TOKEN}
      HEALTH_CHECK_INTERVAL: 86400
    volumes:
      - ./config/pipeline-openwrt.yaml:/home/kernelci/config/pipeline.yaml:ro
      - ./openwrt-pipeline:/home/kernelci/openwrt-pipeline:ro
    command: ["python", "-m", "openwrt_pipeline.health_scheduler"]
    networks:
      - kci-network
    restart: unless-stopped

  # ============================================
  # Dashboard
  # ============================================

  dashboard:
    image: ghcr.io/kernelci/dashboard:latest
    container_name: openwrt-kci-dashboard
    depends_on:
      - kernelci-api
    environment:
      NEXT_PUBLIC_API_URL: http://kernelci-api:8001
      # Enable OpenWrt-specific views
      NEXT_PUBLIC_PROJECT: openwrt
    ports:
      - "3000:3000"
    networks:
      - kci-network
    restart: unless-stopped

  # ============================================
  # Reverse Proxy
  # ============================================

  traefik:
    image: traefik:v3.0
    container_name: openwrt-kci-proxy
    command:
      - "--api.dashboard=true"
      - "--providers.docker=true"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web"
      - "--certificatesresolvers.letsencrypt.acme.email=${ACME_EMAIL}"
      - "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - letsencrypt_data:/letsencrypt
    networks:
      - kci-network
    restart: unless-stopped

volumes:
  mongodb_data:
  redis_data:
  minio_data:
  letsencrypt_data:

networks:
  kci-network:
    driver: bridge
```

### API Configuration

```toml
# config/api-config.toml
[server]
host = "0.0.0.0"
port = 8001

[database]
service = "mongodb://mongodb:27017"
name = "openwrt_kernelci"

[redis]
host = "redis"
port = 6379

[storage]
type = "s3"
endpoint = "http://minio:9000"
bucket = "openwrt-artifacts"
access_key_env = "MINIO_ACCESS_KEY"
secret_key_env = "MINIO_SECRET_KEY"

[jwt]
secret_key_env = "KCI_SECRET_KEY"
algorithm = "HS256"
access_token_expire_minutes = 480

# OpenWrt-specific settings
[openwrt]
project_name = "OpenWrt"
firmware_sources = ["official", "pr", "custom", "buildbot"]
default_test_timeout = 1800
health_check_interval = 86400

[openwrt.official_sources]
snapshot = "https://downloads.openwrt.org/snapshots/targets"
stable = "https://downloads.openwrt.org/releases/24.10.0/targets"
oldstable = "https://downloads.openwrt.org/releases/23.05.5/targets"
```

### Labgrid Adapter Deployment (Per Lab)

```yaml
# labgrid-adapter/docker-compose.yml
version: '3.8'

services:
  labgrid-adapter:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: labgrid-kci-adapter
    environment:
      # KernelCI API connection
      KCI_API_URL: ${KCI_API_URL}  # https://openwrt-kci.example.org/api
      KCI_API_TOKEN: ${KCI_API_TOKEN}
      LAB_NAME: ${LAB_NAME}

      # Labgrid coordinator
      LG_CROSSBAR: ${LG_CROSSBAR:-ws://localhost:20408/ws}

      # Local storage for firmware caching
      FIRMWARE_CACHE: /cache

    volumes:
      - ./config:/app/config:ro
      - ./targets:/app/targets:ro
      - firmware_cache:/cache
      - /var/run/docker.sock:/var/run/docker.sock  # For QEMU targets

    # Host network for labgrid coordinator access
    network_mode: host

    restart: unless-stopped

volumes:
  firmware_cache:
```

```dockerfile
# labgrid-adapter/Dockerfile
FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    openssh-client \
    qemu-system-arm \
    qemu-system-mips \
    qemu-system-x86 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy adapter code
COPY labgrid_kci_adapter/ ./labgrid_kci_adapter/
COPY tests/ ./tests/
COPY conftest.py .

# Entry point
CMD ["python", "-m", "labgrid_kci_adapter.service"]
```

### Environment File

```bash
# .env
# MongoDB
MONGO_PASSWORD=change_me_secure_password

# MinIO (S3-compatible storage)
MINIO_ACCESS_KEY=openwrt-kci
MINIO_SECRET_KEY=change_me_secure_password

# KernelCI API
KCI_SECRET_KEY=change_me_32_char_minimum_secret_key
KCI_API_TOKEN=admin_token_change_me

# TLS certificates
ACME_EMAIL=admin@example.org

# Lab configuration (for adapter)
LAB_NAME=openwrt-lab-1
KCI_API_URL=https://openwrt-kci.example.org
```

---

## Implementation Plan

### Phase 1: Core Infrastructure (Weeks 1-3)

**Goal:** Deploy self-hosted KernelCI with basic OpenWrt support.

| Task | Description | Deliverable |
|------|-------------|-------------|
| 1.1 | Set up Docker Compose stack | Running MongoDB, Redis, MinIO |
| 1.2 | Deploy KernelCI API | API accessible at /api |
| 1.3 | Configure authentication | JWT tokens, user management |
| 1.4 | Deploy dashboard | Basic web UI running |
| 1.5 | Implement OpenWrt firmware schema | Custom node types in API |

### Phase 2: Firmware Sources (Weeks 4-6)

**Goal:** Enable firmware ingestion from multiple sources.

| Task | Description | Deliverable |
|------|-------------|-------------|
| 2.1 | Official release watcher | Auto-import snapshots/releases |
| 2.2 | GitHub PR integration | Import artifacts from PRs |
| 2.3 | Custom upload API | `/api/v1/firmware/upload` endpoint |
| 2.4 | Firmware storage in MinIO | Organized artifact storage |
| 2.5 | profiles.json parser | Extract device features |

### Phase 3: Labgrid Integration (Weeks 7-10)

**Goal:** Connect labgrid infrastructure to KernelCI.

| Task | Description | Deliverable |
|------|-------------|-------------|
| 3.1 | Labgrid adapter service | Pull-mode job execution |
| 3.2 | Device capability mapping | Match jobs to compatible devices |
| 3.3 | Test execution bridge | pytest → KernelCI results |
| 3.4 | Console log upload | Logs in MinIO, linked in results |
| 3.5 | Multi-lab support | Federated lab registration |

### Phase 4: Health Checks (Weeks 11-13)

**Goal:** Implement comprehensive device health monitoring.

| Task | Description | Deliverable |
|------|-------------|-------------|
| 4.1 | Health check scheduler | Periodic checks (daily) |
| 4.2 | Health check executor | Shell/SSH verification |
| 4.3 | Device status tracking | Healthy/failing/disabled states |
| 4.4 | Automated issue creation | GitHub issues for failures |
| 4.5 | Health dashboard view | Visual fleet status |

### Phase 5: Dashboard Customization (Weeks 14-16)

**Goal:** OpenWrt-specific visualization and developer experience.

| Task | Description | Deliverable |
|------|-------------|-------------|
| 5.1 | Firmware comparison view | Version × device × test matrix |
| 5.2 | PR status integration | Test results on PRs |
| 5.3 | Device fleet overview | Map of labs and devices |
| 5.4 | Regression detection | Highlight new failures |
| 5.5 | Developer notifications | Email/Slack on regressions |

---

## Current Infrastructure Preservation

### What Stays the Same

| Component | Current | Proposed |
|-----------|---------|----------|
| Device targets | 38+ YAML files in `/targets/` | Same files, used by adapter |
| Lab network | 7 distributed labs | Same labs, pull-mode adapter |
| Test suite | pytest tests in `/tests/` | Same tests, invoked by adapter |
| labgrid | Custom fork | Continue using |
| Device features | `@pytest.mark.lg_feature` | Mapped to job requirements |

### What Changes

| Component | Current | Proposed |
|-----------|---------|----------|
| Orchestration | GitHub Actions | KernelCI Pipeline |
| Results UI | GitHub Pages | KernelCI Dashboard |
| Job scheduling | GHA matrix | KernelCI Scheduler |
| Health checks | healthcheck.yml | Health Check service |
| Result storage | GHA artifacts | MinIO + PostgreSQL |

---

## References

- [KernelCI Self-Hosted Documentation](https://docs.kernelci.org/components/devops/)
- [KernelCI Local Instance Setup](https://docs.kernelci.org/maestro/api/local-instance/)
- [KernelCI Pipeline Configuration](https://github.com/kernelci/kernelci-pipeline/blob/main/config/pipeline.yaml)
- [KernelCI Docker Containers](https://github.com/kernelci/kernelci-docker)
- [KernelCI Dashboard](https://github.com/kernelci/dashboard)
- [Simple KernelCI Labs with Labgrid (LPC 2022)](https://lpc.events/event/16/contributions/1313/)

---

*Document updated: January 2025*
*Focus: Self-hosted KernelCI for OpenWrt firmware testing*
