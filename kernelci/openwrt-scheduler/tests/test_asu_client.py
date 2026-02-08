"""Tests for ASU (Attended Sysupgrade) client."""

import pytest

from openwrt_scheduler.asu_client import (
    ASU_API_URL,
    ImageBuildRequest,
    ImageBuildResult,
)


class TestImageBuildRequest:
    """Tests for ImageBuildRequest dataclass."""

    def test_basic_request(self):
        """Test creating a basic build request."""
        request = ImageBuildRequest(
            target="ath79",
            subtarget="generic",
            profile="tplink_archer-c7-v2",
            version="SNAPSHOT",
            packages=["bash", "python3"],
        )

        assert request.target == "ath79"
        assert request.subtarget == "generic"
        assert request.profile == "tplink_archer-c7-v2"
        assert request.version == "SNAPSHOT"
        assert request.packages == ["bash", "python3"]

    def test_to_dict(self):
        """Test converting request to API dict format."""
        request = ImageBuildRequest(
            target="x86",
            subtarget="64",
            profile="generic",
            version="23.05.3",
            packages=["bash"],
        )

        data = request.to_dict()

        assert data["target"] == "x86"
        assert data["subtarget"] == "64"
        assert data["profile"] == "generic"
        assert data["version"] == "23.05.3"
        assert data["packages"] == ["bash"]
        assert data["diff_packages"] is False

    def test_to_dict_with_filesystem(self):
        """Test converting request with filesystem option."""
        request = ImageBuildRequest(
            target="x86",
            subtarget="64",
            profile="generic",
            version="SNAPSHOT",
            packages=[],
            filesystem="ext4",
        )

        data = request.to_dict()
        assert data["filesystem"] == "ext4"

    def test_default_packages_empty(self):
        """Test that packages defaults to empty list."""
        request = ImageBuildRequest(
            target="ath79",
            subtarget="generic",
            profile="tplink_archer-c7-v2",
            version="SNAPSHOT",
        )

        assert request.packages == []


class TestImageBuildResult:
    """Tests for ImageBuildResult dataclass."""

    def test_from_response_completed(self):
        """Test parsing completed build response."""
        response = {
            "request_hash": "abc123",
            "status": "completed",
            "version": "23.05.3",
            "target": "ath79/generic",
            "profile": "tplink_archer-c7-v2",
            "images": [
                {
                    "type": "sysupgrade",
                    "url": "https://example.com/sysupgrade.bin",
                    "sha256": "abc123",
                },
                {
                    "type": "factory",
                    "url": "https://example.com/factory.bin",
                    "sha256": "def456",
                },
            ],
            "manifest_url": "https://example.com/manifest.txt",
        }

        result = ImageBuildResult.from_response(response)

        assert result.request_hash == "abc123"
        assert result.status == "completed"
        assert result.version == "23.05.3"
        assert result.sysupgrade_url == "https://example.com/sysupgrade.bin"
        assert result.factory_url == "https://example.com/factory.bin"
        assert result.sha256_sysupgrade == "abc123"
        assert result.sha256_factory == "def456"

    def test_from_response_queued(self):
        """Test parsing queued build response."""
        response = {
            "request_hash": "xyz789",
            "status": "queued",
            "version": "SNAPSHOT",
            "target": "x86/64",
            "profile": "generic",
        }

        result = ImageBuildResult.from_response(response)

        assert result.request_hash == "xyz789"
        assert result.status == "queued"
        assert result.sysupgrade_url is None
        assert result.factory_url is None

    def test_from_response_failed(self):
        """Test parsing failed build response."""
        response = {
            "request_hash": "failed123",
            "status": "failed",
            "version": "SNAPSHOT",
            "target": "ath79/generic",
            "profile": "nonexistent",
            "error": "Profile not found",
        }

        result = ImageBuildResult.from_response(response)

        assert result.status == "failed"
        assert result.error == "Profile not found"


class TestASUClientConstants:
    """Tests for ASU client constants."""

    def test_default_api_url(self):
        """Test default ASU API URL."""
        assert ASU_API_URL == "https://sysupgrade.openwrt.org/api/v1"
