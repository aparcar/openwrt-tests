"""Tests for OpenWrt version discovery."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from openwrt_scheduler.versions import (
    OPENWRT_REPO,
    OPENWRT_TREE,
    BranchInfo,
    extract_major_minor,
    fetch_versions,
    get_active_branches,
    get_tree_info,
    version_to_branch,
)


class TestVersionToBranch:
    """Tests for version_to_branch function."""

    def test_snapshot(self):
        """Test SNAPSHOT maps to main."""
        assert version_to_branch("SNAPSHOT") == "main"

    def test_snapshot_lowercase(self):
        """Test snapshot (lowercase) maps to main."""
        assert version_to_branch("snapshot") == "main"

    def test_release_24_10_0(self):
        """Test 24.10.0 maps to openwrt-24.10."""
        assert version_to_branch("24.10.0") == "openwrt-24.10"

    def test_release_24_10_1(self):
        """Test 24.10.1 maps to openwrt-24.10."""
        assert version_to_branch("24.10.1") == "openwrt-24.10"

    def test_release_23_05_5(self):
        """Test 23.05.5 maps to openwrt-23.05."""
        assert version_to_branch("23.05.5") == "openwrt-23.05"

    def test_release_25_12_0(self):
        """Test 25.12.0 maps to openwrt-25.12."""
        assert version_to_branch("25.12.0") == "openwrt-25.12"

    def test_invalid_version(self):
        """Test invalid version defaults to main."""
        assert version_to_branch("invalid") == "main"

    def test_empty_string(self):
        """Test empty string defaults to main."""
        assert version_to_branch("") == "main"


class TestExtractMajorMinor:
    """Tests for extract_major_minor function."""

    def test_full_version(self):
        """Test extracting from full version."""
        assert extract_major_minor("24.10.0") == (24, 10)

    def test_major_minor_only(self):
        """Test extracting from major.minor only."""
        assert extract_major_minor("24.10") == (24, 10)

    def test_with_suffix(self):
        """Test extracting from version with suffix."""
        assert extract_major_minor("24.10.0-rc1") == (24, 10)

    def test_invalid(self):
        """Test invalid version returns None."""
        assert extract_major_minor("SNAPSHOT") is None
        assert extract_major_minor("invalid") is None


class TestBranchInfo:
    """Tests for BranchInfo dataclass."""

    def test_create(self):
        """Test creating BranchInfo."""
        branch = BranchInfo(
            name="main",
            version="SNAPSHOT",
            url="https://downloads.openwrt.org/snapshots/targets",
            is_snapshot=True,
        )
        assert branch.name == "main"
        assert branch.version == "SNAPSHOT"
        assert branch.is_snapshot is True

    def test_defaults(self):
        """Test BranchInfo defaults."""
        branch = BranchInfo(
            name="openwrt-24.10",
            version="24.10.0",
            url="https://example.com",
        )
        assert branch.is_snapshot is False


class TestFetchVersions:
    """Tests for fetch_versions function."""

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        """Test successful version fetch."""
        mock_response = {
            "stable_version": "24.10.0",
            "versions_list": ["24.10.0", "23.05.5", "23.05.4"],
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_response_obj = AsyncMock()
            mock_response_obj.json.return_value = mock_response
            mock_response_obj.raise_for_status = lambda: None
            mock_client.get.return_value = mock_response_obj
            mock_client_class.return_value = mock_client

            result = await fetch_versions()

            assert result["stable_version"] == "24.10.0"
            assert "24.10.0" in result["versions_list"]

    @pytest.mark.asyncio
    async def test_fetch_network_error(self):
        """Test fetch handles network error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.side_effect = httpx.RequestError("Network error")
            mock_client_class.return_value = mock_client

            with pytest.raises(httpx.RequestError):
                await fetch_versions()


class TestGetActiveBranches:
    """Tests for get_active_branches function."""

    @pytest.mark.asyncio
    async def test_all_branches(self):
        """Test getting all branches."""
        mock_data = {
            "stable_version": "24.10.0",
            "versions_list": ["24.10.0", "23.05.5"],
        }

        with patch(
            "openwrt_scheduler.versions.fetch_versions",
            new_callable=AsyncMock,
            return_value=mock_data,
        ):
            branches = await get_active_branches()

        # Should have main, stable, and oldstable
        assert len(branches) == 3

        names = [b.name for b in branches]
        assert "main" in names
        assert "openwrt-24.10" in names
        assert "openwrt-23.05" in names

    @pytest.mark.asyncio
    async def test_without_snapshot(self):
        """Test getting branches without snapshot."""
        mock_data = {
            "stable_version": "24.10.0",
            "versions_list": ["24.10.0", "23.05.5"],
        }

        with patch(
            "openwrt_scheduler.versions.fetch_versions",
            new_callable=AsyncMock,
            return_value=mock_data,
        ):
            branches = await get_active_branches(include_snapshot=False)

        names = [b.name for b in branches]
        assert "main" not in names
        assert "openwrt-24.10" in names

    @pytest.mark.asyncio
    async def test_without_oldstable(self):
        """Test getting branches without oldstable."""
        mock_data = {
            "stable_version": "24.10.0",
            "versions_list": ["24.10.0", "23.05.5"],
        }

        with patch(
            "openwrt_scheduler.versions.fetch_versions",
            new_callable=AsyncMock,
            return_value=mock_data,
        ):
            branches = await get_active_branches(include_oldstable=False)

        names = [b.name for b in branches]
        assert "main" in names
        assert "openwrt-24.10" in names
        assert "openwrt-23.05" not in names

    @pytest.mark.asyncio
    async def test_fallback_on_error(self):
        """Test fallback when fetch fails."""
        with patch(
            "openwrt_scheduler.versions.fetch_versions",
            new_callable=AsyncMock,
            side_effect=Exception("Network error"),
        ):
            branches = await get_active_branches()

        # Should have fallback versions
        assert len(branches) >= 2

        names = [b.name for b in branches]
        assert "main" in names

    @pytest.mark.asyncio
    async def test_branch_urls(self):
        """Test branch URLs are correct."""
        mock_data = {
            "stable_version": "24.10.0",
            "versions_list": ["24.10.0"],
        }

        with patch(
            "openwrt_scheduler.versions.fetch_versions",
            new_callable=AsyncMock,
            return_value=mock_data,
        ):
            branches = await get_active_branches(include_oldstable=False)

        main_branch = next(b for b in branches if b.name == "main")
        stable_branch = next(b for b in branches if b.name == "openwrt-24.10")

        assert "snapshots" in main_branch.url
        assert "releases/24.10.0" in stable_branch.url


class TestGetTreeInfo:
    """Tests for get_tree_info function."""

    def test_returns_tree_info(self):
        """Test get_tree_info returns correct info."""
        info = get_tree_info()

        assert info["tree"] == OPENWRT_TREE
        assert info["url"] == OPENWRT_REPO

    def test_tree_value(self):
        """Test tree name is openwrt."""
        assert OPENWRT_TREE == "openwrt"

    def test_repo_url(self):
        """Test repo URL is correct."""
        assert "git.openwrt.org" in OPENWRT_REPO
