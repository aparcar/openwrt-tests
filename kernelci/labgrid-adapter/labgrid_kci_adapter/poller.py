"""
Job Poller for KernelCI

Polls the KernelCI API for pending test jobs that match
this lab's capabilities (devices and features).

Uses the KernelCI Node-based API where jobs are nodes with kind=job
and state=available.
"""

import asyncio
import logging
from datetime import datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings

logger = logging.getLogger(__name__)


class JobPoller:
    """
    Polls KernelCI API for pending jobs.

    The poller:
    1. Polls for pending jobs (nodes with kind=job, state=available)
    2. Claims jobs by updating state to 'running'
    3. Dispatches jobs to the executor
    4. Handles concurrent job limits
    """

    def __init__(
        self,
        lab_name: str,
        devices: list[str],
        features: list[str],
        on_job,
    ):
        self.lab_name = lab_name
        self.devices = devices
        self.features = features
        self.on_job = on_job

        self.api_url = settings.kci_api_url.rstrip("/")
        self.api_token = settings.kci_api_token

        self._client: httpx.AsyncClient | None = None
        self._running = False
        self._current_jobs: set[str] = set()

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

    async def _poll_jobs(self) -> list[dict]:
        """
        Poll for pending jobs matching our devices.

        Queries nodes with:
        - kind=job
        - state=available
        - data.runtime=labgrid
        - data.device_type in our devices
        """
        jobs = []

        for device in self.devices:
            if device in self._current_jobs:
                continue

            try:
                # Query for available jobs for this device type
                params = {
                    "kind": "job",
                    "state": "available",
                    "data.device_type": device,
                    "data.runtime": "labgrid",
                    "limit": 1,
                }
                data = await self._api_request("GET", "/latest/nodes", params=params)

                # Handle both list and dict responses
                items = data if isinstance(data, list) else data.get("items", [])
                jobs.extend(items)

            except Exception as e:
                logger.warning(f"Failed to poll jobs for {device}: {e}")

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
                # Check capacity
                if len(self._current_jobs) >= settings.max_concurrent_jobs:
                    logger.debug("At max concurrent jobs, waiting...")
                    await asyncio.sleep(poll_interval)
                    continue

                # Poll for jobs
                jobs = await self._poll_jobs()

                for job in jobs:
                    job_id = job.get("id") or job.get("_id")
                    device = job.get("data", {}).get("device_type")

                    if not job_id or not device:
                        continue

                    if job_id in self._current_jobs:
                        continue

                    # Try to claim
                    if await self._claim_job(job_id, device):
                        self._current_jobs.add(job_id)
                        asyncio.create_task(
                            self._execute_job(job),
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

    async def _execute_job(self, job: dict) -> None:
        """Execute a job and handle completion."""
        job_id = job.get("id") or job.get("_id")

        try:
            await self.on_job(job)
        except Exception as e:
            logger.exception(f"Job {job_id} failed: {e}")
        finally:
            self._current_jobs.discard(job_id)

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
