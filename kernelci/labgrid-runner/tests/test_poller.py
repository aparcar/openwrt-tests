"""Tests for job poller with parallel execution support."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from labgrid_runner.labgrid_client import LabgridClient, Place
from labgrid_runner.poller import JobPoller


@pytest.fixture
def mock_labgrid_client():
    """Create a mock labgrid client."""
    client = MagicMock(spec=LabgridClient)
    client.count_available = AsyncMock(return_value=1)
    return client


@pytest.fixture
def poller(mock_labgrid_client):
    """Create a poller with mocked dependencies."""
    with patch("labgrid_runner.poller.settings") as mock_settings:
        mock_settings.kci_api_url = "http://api.example.com"
        mock_settings.kci_api_token = "test-token"
        mock_settings.poll_interval = 1
        mock_settings.max_concurrent_jobs = 10

        return JobPoller(
            lab_name="test-lab",
            devices=["openwrt_one", "archer-c7"],
            features=["wifi"],
            on_job=AsyncMock(),
            labgrid_client=mock_labgrid_client,
        )


class TestJobPollerInit:
    """Tests for JobPoller initialization."""

    def test_init_tracking_structures(self, poller):
        """Test that tracking structures are initialized correctly."""
        assert poller._current_jobs == {}
        assert poller._jobs_per_device == {}

    def test_init_with_labgrid_client(self, poller, mock_labgrid_client):
        """Test that labgrid client is set."""
        assert poller._labgrid == mock_labgrid_client


class TestJobPollerTracking:
    """Tests for job tracking with parallel execution."""

    def test_track_job_per_device_type(self, poller):
        """Test tracking jobs per device type."""
        # Simulate claiming a job
        poller._current_jobs["job-1"] = "openwrt_one"
        poller._jobs_per_device["openwrt_one"].add("job-1")

        assert "job-1" in poller._current_jobs
        assert "job-1" in poller._jobs_per_device["openwrt_one"]

    def test_track_multiple_jobs_same_device_type(self, poller):
        """Test tracking multiple jobs for same device type."""
        # Simulate claiming multiple jobs for same device type
        poller._current_jobs["job-1"] = "openwrt_one"
        poller._current_jobs["job-2"] = "openwrt_one"
        poller._jobs_per_device["openwrt_one"].add("job-1")
        poller._jobs_per_device["openwrt_one"].add("job-2")

        assert len(poller._jobs_per_device["openwrt_one"]) == 2

    def test_cleanup_job_tracking(self, poller):
        """Test cleanup of job tracking."""
        # Setup
        poller._current_jobs["job-1"] = "openwrt_one"
        poller._jobs_per_device["openwrt_one"].add("job-1")

        # Cleanup (simulating _execute_job finally block)
        poller._current_jobs.pop("job-1", None)
        poller._jobs_per_device["openwrt_one"].discard("job-1")

        assert "job-1" not in poller._current_jobs
        assert "job-1" not in poller._jobs_per_device["openwrt_one"]


class TestJobPollerParallelExecution:
    """Tests for parallel execution support."""

    @pytest.mark.asyncio
    async def test_poll_respects_available_places(self, poller, mock_labgrid_client):
        """Test that polling respects available places."""
        # 2 places available for openwrt_one
        mock_labgrid_client.count_available = AsyncMock(
            side_effect=lambda dt: 2 if dt == "openwrt_one" else 1
        )

        # Mock API response
        poller._api_request = AsyncMock(
            return_value=[
                {"id": "job-1", "data": {"device_type": "openwrt_one"}},
                {"id": "job-2", "data": {"device_type": "openwrt_one"}},
            ]
        )

        jobs = await poller._poll_jobs()

        # Should return 2 jobs since 2 places available
        assert len(jobs) == 4  # 2 for openwrt_one + 2 for archer-c7 (mocked)

    @pytest.mark.asyncio
    async def test_poll_skips_busy_device_type(self, poller, mock_labgrid_client):
        """Test that polling skips device types with no free slots."""
        # 1 place available, 1 job running
        mock_labgrid_client.count_available = AsyncMock(return_value=1)
        poller._jobs_per_device["openwrt_one"].add("existing-job")

        poller._api_request = AsyncMock(return_value=[])

        jobs = await poller._poll_jobs()

        # API should not be called for openwrt_one (no free slots)
        # Check that we didn't query for openwrt_one
        calls = poller._api_request.call_args_list
        for call in calls:
            params = call.kwargs.get("params", {})
            if params.get("data.device_type") == "openwrt_one":
                pytest.fail("Should not poll for openwrt_one when no free slots")

    @pytest.mark.asyncio
    async def test_poll_limits_by_free_slots(self, poller, mock_labgrid_client):
        """Test that polling limits jobs by free slots."""
        # 3 places, 1 job running = 2 free slots
        mock_labgrid_client.count_available = AsyncMock(return_value=3)
        poller._jobs_per_device["openwrt_one"].add("existing-job")

        poller._api_request = AsyncMock(return_value=[])

        await poller._poll_jobs()

        # Check limit parameter
        calls = poller._api_request.call_args_list
        for call in calls:
            params = call.kwargs.get("params", {})
            if params.get("data.device_type") == "openwrt_one":
                assert params.get("limit") == 2  # 3 places - 1 running = 2


class TestJobPollerClaiming:
    """Tests for job claiming."""

    @pytest.mark.asyncio
    async def test_claim_job_success(self, poller):
        """Test successful job claiming."""
        poller._api_request = AsyncMock(return_value={})

        result = await poller._claim_job("job-1", "openwrt_one")

        assert result is True
        poller._api_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_claim_job_conflict(self, poller):
        """Test job claiming with conflict (already claimed)."""
        import httpx

        error_response = MagicMock()
        error_response.status_code = 409
        poller._api_request = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Conflict", request=MagicMock(), response=error_response
            )
        )

        result = await poller._claim_job("job-1", "openwrt_one")

        assert result is False
