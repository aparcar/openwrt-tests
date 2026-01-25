"""
KernelCI API client for OpenWrt Pipeline.

Provides async methods for:
- Firmware management
- Job creation and status updates
- Result submission
- Device and lab management
"""

import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings
from .models import (
    Device,
    DeviceHealthCheck,
    Firmware,
    FirmwareCreate,
    JobCreate,
    JobResult,
    JobUpdate,
    Lab,
    LabHeartbeat,
    LabRegister,
    TestJob,
    TestResult,
)

logger = logging.getLogger(__name__)


class APIError(Exception):
    """API request error."""

    def __init__(self, status_code: int, message: str, details: Any = None):
        self.status_code = status_code
        self.message = message
        self.details = details
        super().__init__(f"API Error {status_code}: {message}")


class KernelCIClient:
    """Async client for KernelCI API."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or settings.kci_api_url).rstrip("/")
        self.token = token or settings.kci_api_token
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def connect(self):
        """Create HTTP client connection."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.timeout),
            )

    async def close(self):
        """Close HTTP client connection."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get the HTTP client, creating if necessary."""
        if self._client is None:
            raise RuntimeError(
                "Client not connected. Use 'async with' or call connect()"
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Make an API request with retry logic."""
        response = await self.client.request(method, path, **kwargs)

        if response.status_code >= 400:
            try:
                error_data = response.json()
            except Exception:
                error_data = {"detail": response.text}

            raise APIError(
                status_code=response.status_code,
                message=error_data.get("detail", "Unknown error"),
                details=error_data,
            )

        if response.status_code == 204:
            return {}

        return response.json()

    # =========================================================================
    # Firmware Operations
    # =========================================================================

    async def create_firmware(self, firmware: FirmwareCreate) -> Firmware:
        """Create a new firmware entry."""
        data = await self._request(
            "POST",
            "/api/v1/firmware",
            json=firmware.model_dump(exclude_none=True),
        )
        return Firmware(**data)

    async def get_firmware(self, firmware_id: str) -> Firmware | None:
        """Get firmware by ID."""
        try:
            data = await self._request("GET", f"/api/v1/firmware/{firmware_id}")
            return Firmware(**data)
        except APIError as e:
            if e.status_code == 404:
                return None
            raise

    async def list_firmware(
        self,
        source: str | None = None,
        version: str | None = None,
        target: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Firmware]:
        """List firmware with optional filters."""
        params = {"limit": limit, "offset": offset}
        if source:
            params["source"] = source
        if version:
            params["version"] = version
        if target:
            params["target"] = target

        data = await self._request("GET", "/api/v1/firmware", params=params)
        return [Firmware(**item) for item in data.get("items", [])]

    async def firmware_exists(
        self,
        target: str,
        subtarget: str,
        profile: str,
        version: str,
        git_commit: str | None = None,
    ) -> bool:
        """Check if firmware already exists."""
        params = {
            "target": target,
            "subtarget": subtarget,
            "profile": profile,
            "version": version,
        }
        if git_commit:
            params["git_commit_hash"] = git_commit

        data = await self._request("GET", "/api/v1/firmware/exists", params=params)
        return data.get("exists", False)

    # =========================================================================
    # Job Operations
    # =========================================================================

    async def create_job(self, job: JobCreate) -> TestJob:
        """Create a new test job."""
        data = await self._request(
            "POST",
            "/api/v1/jobs",
            json=job.model_dump(exclude_none=True),
        )
        return TestJob(**data)

    async def get_job(self, job_id: str) -> TestJob | None:
        """Get job by ID."""
        try:
            data = await self._request("GET", f"/api/v1/jobs/{job_id}")
            return TestJob(**data)
        except APIError as e:
            if e.status_code == 404:
                return None
            raise

    async def list_pending_jobs(
        self,
        device_type: str | None = None,
        lab_name: str | None = None,
        limit: int = 10,
    ) -> list[TestJob]:
        """List pending jobs for a lab/device."""
        params = {"status": "pending", "limit": limit}
        if device_type:
            params["device_type"] = device_type
        if lab_name:
            params["lab_name"] = lab_name

        data = await self._request("GET", "/api/v1/jobs/pending", params=params)
        return [TestJob(**item) for item in data.get("items", [])]

    async def update_job(self, job_id: str, update: JobUpdate) -> TestJob:
        """Update job status."""
        data = await self._request(
            "PATCH",
            f"/api/v1/jobs/{job_id}",
            json=update.model_dump(exclude_none=True),
        )
        return TestJob(**data)

    async def start_job(self, job_id: str, lab_name: str, device_id: str) -> TestJob:
        """Mark a job as started."""
        from datetime import datetime

        return await self.update_job(
            job_id,
            JobUpdate(
                status="running",
                assigned_lab=lab_name,
                assigned_device=device_id,
                started_at=datetime.utcnow(),
            ),
        )

    async def complete_job(self, job_id: str, result: JobResult) -> TestJob:
        """Submit job completion with results."""
        data = await self._request(
            "POST",
            f"/api/v1/jobs/{job_id}/complete",
            json=result.model_dump(exclude_none=True, mode="json"),
        )
        return TestJob(**data)

    # =========================================================================
    # Result Operations
    # =========================================================================

    async def submit_result(self, result: TestResult) -> TestResult:
        """Submit a single test result."""
        data = await self._request(
            "POST",
            "/api/v1/results",
            json=result.model_dump(exclude_none=True, mode="json"),
        )
        return TestResult(**data)

    async def submit_results(self, results: list[TestResult]) -> list[TestResult]:
        """Submit multiple test results."""
        data = await self._request(
            "POST",
            "/api/v1/results/batch",
            json=[r.model_dump(exclude_none=True, mode="json") for r in results],
        )
        return [TestResult(**item) for item in data.get("items", [])]

    async def get_results(
        self,
        firmware_id: str | None = None,
        device_type: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[TestResult]:
        """Get test results with filters."""
        params = {"limit": limit}
        if firmware_id:
            params["firmware_id"] = firmware_id
        if device_type:
            params["device_type"] = device_type
        if job_id:
            params["job_id"] = job_id

        data = await self._request("GET", "/api/v1/results", params=params)
        return [TestResult(**item) for item in data.get("items", [])]

    # =========================================================================
    # Device Operations
    # =========================================================================

    async def register_device(self, device: Device) -> Device:
        """Register or update a device."""
        data = await self._request(
            "POST",
            "/api/v1/devices",
            json=device.model_dump(exclude_none=True, mode="json"),
        )
        return Device(**data)

    async def get_device(self, device_id: str) -> Device | None:
        """Get device by ID."""
        try:
            data = await self._request("GET", f"/api/v1/devices/{device_id}")
            return Device(**data)
        except APIError as e:
            if e.status_code == 404:
                return None
            raise

    async def list_devices(
        self,
        lab_name: str | None = None,
        status: str | None = None,
    ) -> list[Device]:
        """List devices with filters."""
        params = {}
        if lab_name:
            params["lab_name"] = lab_name
        if status:
            params["status"] = status

        data = await self._request("GET", "/api/v1/devices", params=params)
        return [Device(**item) for item in data.get("items", [])]

    async def update_device_status(
        self,
        device_id: str,
        status: str,
        consecutive_failures: int | None = None,
    ) -> Device:
        """Update device health status."""
        payload = {"status": status}
        if consecutive_failures is not None:
            payload["consecutive_failures"] = consecutive_failures

        data = await self._request(
            "PATCH",
            f"/api/v1/devices/{device_id}",
            json=payload,
        )
        return Device(**data)

    async def submit_health_check(
        self,
        health_check: DeviceHealthCheck,
    ) -> DeviceHealthCheck:
        """Submit device health check result."""
        data = await self._request(
            "POST",
            "/api/v1/health-checks",
            json=health_check.model_dump(exclude_none=True, mode="json"),
        )
        return DeviceHealthCheck(**data)

    # =========================================================================
    # Lab Operations
    # =========================================================================

    async def register_lab(self, lab: LabRegister) -> Lab:
        """Register a new lab."""
        data = await self._request(
            "POST",
            "/api/v1/labs/register",
            json=lab.model_dump(exclude_none=True),
        )
        return Lab(**data)

    async def get_lab(self, lab_id: str) -> Lab | None:
        """Get lab by ID."""
        try:
            data = await self._request("GET", f"/api/v1/labs/{lab_id}")
            return Lab(**data)
        except APIError as e:
            if e.status_code == 404:
                return None
            raise

    async def lab_heartbeat(self, heartbeat: LabHeartbeat) -> Lab:
        """Send lab heartbeat."""
        data = await self._request(
            "POST",
            f"/api/v1/labs/{heartbeat.lab_id}/heartbeat",
            json=heartbeat.model_dump(exclude_none=True),
        )
        return Lab(**data)

    async def list_labs(self, status: str | None = None) -> list[Lab]:
        """List all labs."""
        params = {}
        if status:
            params["status"] = status

        data = await self._request("GET", "/api/v1/labs", params=params)
        return [Lab(**item) for item in data.get("items", [])]

    # =========================================================================
    # Events
    # =========================================================================

    async def publish_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to the event bus."""
        await self._request(
            "POST",
            "/api/v1/events",
            json={"type": event_type, "data": data},
        )

    async def subscribe_events(
        self,
        event_types: list[str],
    ):
        """Subscribe to events (returns async generator)."""
        # This would use WebSocket or SSE in a real implementation
        # For now, we'll use polling
        pass
