"""
Data models for OpenWrt KernelCI Pipeline.

These Pydantic models define the structure of:
- Firmware metadata
- Test jobs
- Test results
- Device status
- Lab registration
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# =============================================================================
# Enums
# =============================================================================


class FirmwareSource(str, Enum):
    """Firmware source types."""

    OFFICIAL = "official"
    PR = "pr"
    CUSTOM = "custom"
    BUILDBOT = "buildbot"


class JobStatus(str, Enum):
    """Job status values."""

    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class TestStatus(str, Enum):
    """Test result status values."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


class DeviceStatus(str, Enum):
    """Device health status values."""

    HEALTHY = "healthy"
    FAILING = "failing"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class LabStatus(str, Enum):
    """Lab status values."""

    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


# =============================================================================
# Firmware Models
# =============================================================================


class FirmwareArtifacts(BaseModel):
    """Firmware artifact URLs."""

    sysupgrade: str | None = None
    factory: str | None = None
    initramfs: str | None = None
    kernel: str | None = None
    rootfs: str | None = None
    combined: str | None = None
    manifest: str | None = None

    # SHA256 checksums
    sysupgrade_sha256: str | None = None
    factory_sha256: str | None = None
    initramfs_sha256: str | None = None
    combined_sha256: str | None = None


class Firmware(BaseModel):
    """OpenWrt firmware metadata."""

    id: str = Field(..., description="Unique firmware identifier")
    origin: str = Field(default="openwrt")

    # Source information
    source: FirmwareSource
    source_url: str | None = None
    source_ref: str | None = None  # PR number, buildbot ID, etc.

    # OpenWrt identification
    version: str = Field(..., description="Version string (SNAPSHOT, 24.10.0, etc.)")
    target: str = Field(..., description="Target platform (ath79, mediatek, etc.)")
    subtarget: str = Field(..., description="Subtarget (generic, filogic, etc.)")
    profile: str = Field(..., description="Device profile name")

    # Git information
    git_repository_url: str = Field(default="https://github.com/openwrt/openwrt")
    git_commit_hash: str | None = None
    git_branch: str | None = None

    # Artifacts
    artifacts: FirmwareArtifacts = Field(default_factory=FirmwareArtifacts)

    # Metadata
    build_time: datetime | None = None
    file_size: int | None = None
    sha256: str | None = None
    features: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    description: str | None = None

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FirmwareCreate(BaseModel):
    """Request model for creating firmware entries."""

    source: FirmwareSource
    version: str
    target: str
    subtarget: str
    profile: str
    artifacts: FirmwareArtifacts | None = None
    git_commit_hash: str | None = None
    git_branch: str | None = None
    source_url: str | None = None
    source_ref: str | None = None
    description: str | None = None


# =============================================================================
# Job Models
# =============================================================================


class TestJob(BaseModel):
    """Test job definition."""

    id: str = Field(..., description="Unique job identifier")
    firmware_id: str = Field(..., description="Reference to firmware")

    # Target device
    device_type: str = Field(..., description="Labgrid target name")

    # Test configuration
    test_plan: str = Field(..., description="Test plan name")
    tests: list[str] = Field(default_factory=list, description="Specific tests to run")
    required_features: list[str] = Field(default_factory=list)
    timeout: int = Field(default=1800, description="Job timeout in seconds")

    # Priority and scheduling
    priority: int = Field(
        default=5, description="Job priority (1-10, higher=more urgent)"
    )
    status: JobStatus = Field(default=JobStatus.PENDING)

    # Assignment
    assigned_lab: str | None = None
    assigned_device: str | None = None

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    # Options
    skip_firmware_flash: bool = Field(default=False)
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=2)


class JobCreate(BaseModel):
    """Request model for creating test jobs."""

    firmware_id: str
    device_type: str
    test_plan: str
    tests: list[str] | None = None
    priority: int = 5
    timeout: int = 1800
    skip_firmware_flash: bool = False


class JobUpdate(BaseModel):
    """Request model for updating job status."""

    status: JobStatus
    assigned_lab: str | None = None
    assigned_device: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


# =============================================================================
# Result Models
# =============================================================================


class TestResult(BaseModel):
    """Individual test result."""

    id: str = Field(..., description="Unique result identifier")
    job_id: str
    firmware_id: str
    device_type: str
    lab_name: str

    # Test identification
    test_name: str
    test_path: str | None = None  # Full pytest path

    # Result
    status: TestStatus
    duration: float = Field(..., description="Duration in seconds")
    start_time: datetime
    end_time: datetime | None = None

    # Output
    log_url: str | None = None
    console_log_url: str | None = None
    error_message: str | None = None
    stdout: str | None = None
    stderr: str | None = None

    # Environment
    environment: dict[str, Any] = Field(default_factory=dict)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


class JobResult(BaseModel):
    """Complete job result with all test results."""

    job_id: str
    firmware_id: str
    device_type: str
    lab_name: str

    # Overall status
    status: JobStatus
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    skipped_tests: int = 0
    error_tests: int = 0

    # Timing
    started_at: datetime
    completed_at: datetime
    duration: float

    # Individual results
    test_results: list[TestResult] = Field(default_factory=list)

    # Logs
    console_log_url: str | None = None

    # Environment
    environment: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Device Models
# =============================================================================


class Device(BaseModel):
    """Device registration and status."""

    id: str = Field(..., description="Unique device identifier (labgrid target name)")
    lab_name: str

    # Device type mapping
    target: str
    subtarget: str
    profile: str | None = None

    # Features
    features: list[str] = Field(default_factory=list)

    # Status
    status: DeviceStatus = Field(default=DeviceStatus.UNKNOWN)
    last_check: datetime | None = None
    last_pass: datetime | None = None
    consecutive_failures: int = Field(default=0)

    # Current job
    current_job_id: str | None = None

    # Metadata
    description: str | None = None
    location: str | None = None

    # Timestamps
    registered_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DeviceHealthCheck(BaseModel):
    """Device health check result."""

    device_id: str
    lab_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Result
    status: TestStatus
    checks: list[dict[str, Any]] = Field(default_factory=list)
    duration: float

    # Diagnostics
    error_message: str | None = None
    console_log_url: str | None = None


# =============================================================================
# Lab Models
# =============================================================================


class Lab(BaseModel):
    """Lab registration and status."""

    id: str = Field(..., description="Unique lab identifier")
    name: str

    # Status
    status: LabStatus = Field(default=LabStatus.OFFLINE)
    last_seen: datetime | None = None

    # Capabilities
    devices: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)

    # Configuration
    max_concurrent_jobs: int = Field(default=3)
    coordinator_url: str | None = None

    # Statistics
    jobs_completed: int = Field(default=0)
    jobs_failed: int = Field(default=0)

    # Timestamps
    registered_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class LabRegister(BaseModel):
    """Request model for lab registration."""

    name: str
    devices: list[str]
    features: list[str] = Field(default_factory=list)
    max_concurrent_jobs: int = 3
    coordinator_url: str | None = None


class LabHeartbeat(BaseModel):
    """Lab heartbeat with status update."""

    lab_id: str
    status: LabStatus
    available_devices: list[str] = Field(default_factory=list)
    running_jobs: list[str] = Field(default_factory=list)
