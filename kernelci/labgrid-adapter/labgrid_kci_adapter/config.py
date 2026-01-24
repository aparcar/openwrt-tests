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
        default="http://localhost:8001",
        description="KernelCI API URL",
    )
    kci_api_token: str = Field(
        default="",
        description="KernelCI API authentication token",
    )

    # Labgrid coordinator
    lg_crossbar: str = Field(
        default="ws://localhost:20408/ws",
        description="Labgrid coordinator WebSocket URL",
    )

    # MinIO storage (optional)
    minio_endpoint: str = Field(default="")
    minio_access_key: str = Field(default="")
    minio_secret_key: str = Field(default="")
    minio_secure: bool = Field(default=False)

    # Polling configuration
    poll_interval: int = Field(
        default=30,
        description="Seconds between job polls",
    )
    max_concurrent_jobs: int = Field(
        default=3,
        description="Maximum concurrent jobs",
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

    # Logging
    log_level: str = Field(default="INFO")

    class Config:
        env_prefix = ""
        case_sensitive = False


# Global settings instance
settings = Settings()
