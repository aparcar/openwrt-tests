"""
Configuration for Labgrid KernelCI Adapter.
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Adapter settings from environment variables."""

    # Lab identification
    lab_name: str = Field(
        default="openwrt-lab",
        description="Unique name for this lab",
    )

    # KernelCI API connection
    kci_api_url: str = Field(
        default="http://localhost:8000",
        description="KernelCI API URL",
    )
    kci_api_token: str = Field(
        default="",
        description="KernelCI API authentication token",
    )

    # Labgrid coordinator (gRPC)
    lg_coordinator: str = Field(
        default="localhost:20408",
        description="Labgrid coordinator gRPC address (host:port)",
    )

    # MinIO storage (optional)
    minio_endpoint: str = Field(default="")
    minio_access_key: str = Field(default="")
    minio_secret_key: str = Field(default="")
    minio_secure: bool = Field(default=False)
    minio_logs_bucket: str = Field(
        default="test-logs",
        description="MinIO bucket name for test logs",
    )

    # Polling configuration
    poll_interval: int = Field(
        default=30,
        description="Seconds between job polls",
    )
    max_concurrent_jobs: int = Field(
        default=3,
        description="Maximum concurrent jobs",
    )

    # Health check configuration
    health_check_interval: int = Field(
        default=86400,
        description="Seconds between health checks (default: 24h)",
    )
    health_check_enabled: bool = Field(
        default=True,
        description="Enable automatic health checks",
    )

    # Device discovery configuration
    device_discovery_interval: int = Field(
        default=300,
        description="Seconds between device discovery refreshes (default: 5min)",
    )
    require_target_files: bool = Field(
        default=True,
        description="Only accept jobs for devices with target YAML files",
    )

    # Paths
    targets_dir: Path = Field(
        default=Path("/app/targets"),
        description="Directory containing labgrid target YAML files",
    )
    tests_dir: Path = Field(
        default=Path("/app/tests"),
        description="Directory containing pytest test files",
    )
    firmware_cache: Path = Field(
        default=Path("/app/cache"),
        description="Directory for caching firmware files",
    )

    # Test repository (pulled before each job execution)
    tests_repo_url: str = Field(
        default="",
        description="Git URL for tests repository (if empty, uses local tests_dir)",
    )
    tests_repo_branch: str = Field(
        default="main",
        description="Branch to use for tests repository",
    )
    tests_repo_subdir: str = Field(
        default="",
        description="Subdirectory within tests repository containing tests",
    )

    # Test type configuration
    supported_test_types: str = Field(
        default="firmware",
        description="Comma-separated list of test types this lab supports",
    )

    # Logging
    log_level: str = Field(default="INFO")

    def get_supported_test_types(self) -> list[str]:
        """Get list of supported test types."""
        return [t.strip() for t in self.supported_test_types.split(",") if t.strip()]

    class Config:
        env_file = ".env"
        env_prefix = ""
        case_sensitive = False


# Global settings instance
settings = Settings()
