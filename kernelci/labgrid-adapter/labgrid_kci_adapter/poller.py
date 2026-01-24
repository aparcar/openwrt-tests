"""
Job Poller for KernelCI

Polls the KernelCI API for pending test jobs that match
this lab's capabilities (devices and features).

This implements the "pull-mode" architecture where labs
pull jobs from KernelCI rather than KernelCI pushing to labs.
"""

import asyncio
import logging
from datetime import datetime
from typing import Callable

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings

logger = logging.getLogger(__name__)


class JobPoller:
    """
    Polls KernelCI API for pending jobs.

    The poller:
    1. Registers the lab with KernelCI
    2. Sends periodic heartbeats
    3. Polls for pending jobs matching our capabilities
    4. Dispatches jobs to the executor
    """

    def __init__(
        self,
        lab_name: str,
        devices: list[str],
        features: list[str],
        on_job: Callable,
    ):
        """
        Initialize the job poller.

        Args:
            lab_name: Unique name for this lab
            devices: List of device types available in this lab
            features: List of features supported (wifi, wan_port, etc.)
            on_job: Callback function when a job is received
        """
        self.lab_name = lab_name
        self.devices = devices
        self.features = features
        self.on_job = on_job

        self.api_url = settings.kci_api_url.rstrip("/")
        self.api_token = settings.kci_api_token

        self._client: httpx.AsyncClient | None = None
        self._running = False
        self._current_jobs: set[str] = set()

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def connect(self) -> None:
        """Create HTTP client and register with API."""
        self._client = httpx.AsyncClient(
            base_url=self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )

        # Register lab
        await self._register_lab()

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get HTTP client."""
        if self._client is None:
            raise RuntimeError("Poller not connected")
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _api_request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> dict:
        """Make an API request with retry logic."""
        response = await self.client.request(method, path, **kwargs)

        if response.status_code >= 400:
            logger.error(
                f"API error: {response.status_code} - {response.text}",
                extra={"path": path, "method": method},
            )
            response.raise_for_status()

        if response.status_code == 204:
            return {}

        return response.json()

    async def _register_lab(self) -> None:
        """Register this lab with the KernelCI API."""
        logger.info(f"Registering lab: {self.lab_name}")

        try:
            data = await self._api_request(
                "POST",
                "/api/v1/labs/register",
                json={
                    "name": self.lab_name,
                    "devices": self.devices,
                    "features": self.features,
                    "max_concurrent_jobs": settings.max_concurrent_jobs,
                },
            )
            logger.info(f"Lab registered: {data.get('id', self.lab_name)}")
        except Exception as e:
            logger.error(f"Failed to register lab: {e}")
            raise

    async def _send_heartbeat(self) -> None:
        """Send heartbeat to indicate lab is alive."""
        try:
            await self._api_request(
                "POST",
                f"/api/v1/labs/{self.lab_name}/heartbeat",
                json={
                    "lab_id": self.lab_name,
                    "status": "online",
                    "available_devices": [
                        d for d in self.devices if d not in self._current_jobs
                    ],
                    "running_jobs": list(self._current_jobs),
                },
            )
            logger.debug("Heartbeat sent")
        except Exception as e:
            logger.warning(f"Failed to send heartbeat: {e}")

    async def _poll_jobs(self) -> list[dict]:
        """Poll for pending jobs matching our capabilities."""
        jobs = []

        for device in self.devices:
            # Skip if device is busy
            if device in self._current_jobs:
                continue

            try:
                data = await self._api_request(
                    "GET",
                    "/api/v1/jobs/pending",
                    params={
                        "device_type": device,
                        "lab_name": self.lab_name,
                        "limit": 1,
                    },
                )

                items = data.get("items", [])
                if items:
                    jobs.extend(items)

            except Exception as e:
                logger.warning(f"Failed to poll jobs for {device}: {e}")

        return jobs

    async def _claim_job(self, job_id: str, device: str) -> bool:
        """Attempt to claim a job for execution."""
        try:
            await self._api_request(
                "POST",
                f"/api/v1/jobs/{job_id}/start",
                json={
                    "lab_name": self.lab_name,
                    "device_id": device,
                    "started_at": datetime.utcnow().isoformat(),
                },
            )
            logger.info(f"Claimed job {job_id} for device {device}")
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                # Job already claimed by another lab
                logger.debug(f"Job {job_id} already claimed")
            else:
                logger.warning(f"Failed to claim job {job_id}: {e}")
            return False
        except Exception as e:
            logger.warning(f"Failed to claim job {job_id}: {e}")
            return False

    async def run(self) -> None:
        """
        Main polling loop.

        Continuously polls for jobs and dispatches them to the executor.
        """
        self._running = True
        logger.info(f"Starting job poller for lab: {self.lab_name}")
        logger.info(f"Devices: {self.devices}")
        logger.info(f"Features: {self.features}")

        heartbeat_interval = 60  # seconds
        poll_interval = settings.poll_interval  # seconds

        last_heartbeat = datetime.min

        while self._running:
            try:
                # Send heartbeat periodically
                now = datetime.utcnow()
                if (now - last_heartbeat).total_seconds() >= heartbeat_interval:
                    await self._send_heartbeat()
                    last_heartbeat = now

                # Check if we can accept more jobs
                if len(self._current_jobs) >= settings.max_concurrent_jobs:
                    logger.debug("At max concurrent jobs, waiting...")
                    await asyncio.sleep(poll_interval)
                    continue

                # Poll for jobs
                jobs = await self._poll_jobs()

                for job in jobs:
                    job_id = job.get("id")
                    device = job.get("device_type")

                    if not job_id or not device:
                        continue

                    # Skip if already running
                    if job_id in self._current_jobs:
                        continue

                    # Try to claim the job
                    if await self._claim_job(job_id, device):
                        self._current_jobs.add(job_id)

                        # Dispatch to executor
                        try:
                            asyncio.create_task(
                                self._execute_job(job),
                                name=f"job-{job_id}",
                            )
                        except Exception as e:
                            logger.error(f"Failed to dispatch job {job_id}: {e}")
                            self._current_jobs.discard(job_id)

                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                logger.info("Poller cancelled")
                break
            except Exception as e:
                logger.exception(f"Error in polling loop: {e}")
                await asyncio.sleep(poll_interval)

        logger.info("Job poller stopped")

    async def _execute_job(self, job: dict) -> None:
        """Execute a job and handle completion."""
        job_id = job.get("id")

        try:
            # Call the job handler
            await self.on_job(job)
        except Exception as e:
            logger.exception(f"Job {job_id} failed: {e}")
        finally:
            # Remove from current jobs
            self._current_jobs.discard(job_id)

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
