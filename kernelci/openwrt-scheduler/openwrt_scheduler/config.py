"""
Configuration management for OpenWrt KernelCI Pipeline.

Loads configuration from:
1. Environment variables
2. YAML configuration file (config/pipeline.yaml)
"""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Pipeline service settings from environment variables."""

    # API Configuration
    kci_api_url: str = Field(
        default="http://kernelci-api:8000",
        description="KernelCI API URL",
    )
    kci_api_token: str = Field(
        default="",
        description="KernelCI API authentication token",
    )

    # MinIO Configuration
    minio_endpoint: str = Field(default="minio:9000")
    minio_access_key: str = Field(default="")
    minio_secret_key: str = Field(default="")
    minio_secure: bool = Field(default=False)
    minio_firmware_bucket: str = Field(default="firmware")
    storage_url: str = Field(
        default="",
        description="Public URL for storage (e.g. https://storage.example.org)",
    )

    # GitHub Configuration
    github_token: str | None = Field(default=None)
    github_repo: str = Field(default="openwrt/openwrt")

    # Health Check Configuration
    health_check_interval: int = Field(
        default=86400,
        description="Health check interval in seconds",
    )

    # Logging
    log_level: str = Field(default="INFO")

    # Config file path
    config_file: Path = Field(default=Path("/app/config/pipeline.yaml"))

    class Config:
        env_prefix = ""
        case_sensitive = False


# Global settings instance
settings = Settings()


def load_pipeline_config() -> dict[str, Any]:
    """Load pipeline configuration from YAML file."""
    config_path = settings.config_file

    if not config_path.exists():
        # Try alternative paths
        alt_paths = [
            Path("config/pipeline.yaml"),
            Path("../config/pipeline.yaml"),
            Path("/app/config/pipeline.yaml"),
        ]
        for alt in alt_paths:
            if alt.exists():
                config_path = alt
                break

    if not config_path.exists():
        raise FileNotFoundError(f"Pipeline config not found: {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Substitute environment variables in config
    config = _substitute_env_vars(config)

    return config


def _substitute_env_vars(obj: Any) -> Any:
    """Recursively substitute ${VAR} patterns with environment variables."""
    if isinstance(obj, str):
        if obj.startswith("${") and obj.endswith("}"):
            var_name = obj[2:-1]
            # Handle default values: ${VAR:-default}
            if ":-" in var_name:
                var_name, default = var_name.split(":-", 1)
                return os.environ.get(var_name, default)
            return os.environ.get(var_name, obj)
        return obj
    elif isinstance(obj, dict):
        return {k: _substitute_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_substitute_env_vars(item) for item in obj]
    return obj


def get_device_type(config: dict, device_name: str) -> dict | None:
    """Get device type configuration by name."""
    return config.get("device_types", {}).get(device_name)


def get_firmware_source(config: dict, source_name: str) -> dict | None:
    """Get firmware source configuration by name."""
    return config.get("firmware_sources", {}).get(source_name)
