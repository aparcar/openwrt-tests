"""
Job Poller for KernelCI

Polls the KernelCI API for pending test jobs that match
this lab's capabilities (devices and features).

Uses the KernelCI Node-based API where jobs are nodes with kind=job
and state=available.

Supports parallel execution: if a lab has multiple physical devices
of the same type, it can run multiple jobs for different firmware
versions in parallel.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings
from .labgrid_client import LabgridClient

logger = logging.getLogger(__name__)


class JobPoller:
    """
    Polls KernelCI API for pending jobs.

    The poller:
    1. Polls for pending jobs (nodes with kind=job, state=available)
    2. Claims jobs by updating state to 'running'
    3. Dispatches jobs to the executor
    4. Handles concurrent job limits
    5. Supports parallel execution across multiple devices of same type
    """

    def __init__(
        self,
        lab_name: str,
        devices: list[str],
        features: list[str],
        on_job,
        labgrid_client: LabgridClient | None = None,
    ):
        self.lab_name = lab_name
        self.devices = devices
        self.features = features
        self.on_job = on_job

        self.api_url = settings.kci_api_url.rstrip("/")
        self.api_token = settings.kci_api_token

        self._client: httpx.AsyncClient | None = None
        self._running = False
        # Track running jobs: job_id -> device_type
        self._current_jobs: dict[str, str] = {}
        # Track jobs per device type for parallel execution
        self._jobs_per_device: dict[str, set[str]] = defaultdict(set)
        # Labgrid client to query available places
        self._labgrid = labgrid_client or LabgridClient()

    async def connect(self) -> None:
        """Create HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )
        logger.info(f"Poller connected to {self.api_url}")

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
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
            logger.error(f"API error: {response.status_code} - {response.text}")
            response.raise_for_status()

        if response.status_code == 204:
            return {}

        return response.json()

    async def _poll_jobs(self) -> list[tuple[dict, str]]:
        """
        Poll for pending jobs matching our devices.

        Queries nodes with:
        - kind=job
        - state=available
        - data.runtime=labgrid
        - data.device_type in our devices

        Supports parallel execution: if multiple physical devices of the
        same type are available, fetches multiple jobs for that type.

        Returns:
            List of (job, device_type) tuples
        """
        jobs = []

        for device_type in self.devices:
            try:
                # Check how many places are available for this device type
                available_places = await self._labgrid.count_available(device_type)
                running_jobs = len(self._jobs_per_device.get(device_type, set()))
                free_slots = available_places - running_jobs

                if free_slots <= 0:
                    logger.debug(
                        f"No free slots for {device_type}: "
                        f"{available_places} places, {running_jobs} jobs running"
                    )
                    continue

                # Query for available jobs (up to number of free slots)
                params = {
                    "kind": "job",
                    "state": "available",
                    "data.device_type": device_type,
                    "data.runtime": "labgrid",
                    "limit": free_slots,
                }
                data = await self._api_request("GET", "/latest/nodes", params=params)

                # Handle both list and dict responses
                items = data if isinstance(data, list) else data.get("items", [])

                for job in items:
                    jobs.append((job, device_type))

                if items:
                    logger.debug(
                        f"Found {len(items)} jobs for {device_type} "
                        f"({free_slots} free slots)"
                    )

            except Exception as e:
                logger.warning(f"Failed to poll jobs for {device_type}: {e}")

        return jobs

    async def _claim_job(self, job_id: str, device: str) -> bool:
        """
        Claim a job by updating its state to 'running'.

        Returns True if successfully claimed, False if already taken.
        """
        try:
            await self._api_request(
                "PUT",
                f"/latest/nodes/{job_id}",
                json={
                    "state": "running",
                    "data": {
                        "lab_name": self.lab_name,
                        "device_id": device,
                        "started_at": datetime.utcnow().isoformat(),
                    },
                },
            )
            logger.info(f"Claimed job {job_id} for device {device}")
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (409, 400):
                # Job already claimed or invalid state transition
                logger.debug(f"Job {job_id} already claimed")
            else:
                logger.warning(f"Failed to claim job {job_id}: {e}")
            return False
        except Exception as e:
            logger.warning(f"Failed to claim job {job_id}: {e}")
            return False

    async def run(self) -> None:
        """Main polling loop."""
        self._running = True
        logger.info(f"Starting job poller for lab: {self.lab_name}")
        logger.info(f"Devices: {self.devices}")
        logger.info(f"Features: {self.features}")

        poll_interval = settings.poll_interval

        while self._running:
            try:
                # Check global capacity
                if len(self._current_jobs) >= settings.max_concurrent_jobs:
                    logger.debug("At max concurrent jobs, waiting...")
                    await asyncio.sleep(poll_interval)
                    continue

                # Poll for jobs (returns list of (job, device_type) tuples)
                job_tuples = await self._poll_jobs()

                for job, device_type in job_tuples:
                    # Check global capacity again (might have filled up)
                    if len(self._current_jobs) >= settings.max_concurrent_jobs:
                        break

                    job_id = job.get("id") or job.get("_id")

                    if not job_id:
                        continue

                    if job_id in self._current_jobs:
                        continue

                    # Try to claim
                    if await self._claim_job(job_id, device_type):
                        self._current_jobs[job_id] = device_type
                        self._jobs_per_device[device_type].add(job_id)
                        asyncio.create_task(
                            self._execute_job(job, device_type),
                            name=f"job-{job_id}",
                        )

                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                logger.info("Poller cancelled")
                break
            except Exception as e:
                logger.exception(f"Error in polling loop: {e}")
                await asyncio.sleep(poll_interval)

        logger.info("Job poller stopped")

    async def _execute_job(self, job: dict, device_type: str) -> None:
        """Execute a job and handle completion."""
        job_id = job.get("id") or job.get("_id")

        try:
            await self.on_job(job)
        except Exception as e:
            logger.exception(f"Job {job_id} failed: {e}")
        finally:
            # Clean up job tracking
            self._current_jobs.pop(job_id, None)
            if device_type in self._jobs_per_device:
                self._jobs_per_device[device_type].discard(job_id)

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
