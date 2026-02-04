"""
Labgrid KernelCI Adapter Service

Main service that:
1. Discovers available devices from labgrid
2. Registers with KernelCI API
3. Polls for and executes test jobs
4. Submits results back to KernelCI
5. Runs periodic health checks on devices (every 24h by default)
"""

import asyncio
import os
import signal

import httpx
import structlog

from .config import settings
from .device_discovery import DeviceDiscoveryManager
from .executor import TestExecutor
from .labgrid_client import LabgridClient
from .models import JobResult
from .poller import JobPoller

# Configure logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


class LabgridKCIAdapter:
    """
    Main adapter service connecting labgrid to KernelCI.
    """

    def __init__(self):
        self.lab_name = settings.lab_name
        self.devices: list[str] = []
        self.healthy_devices: set[str] = set()  # Only healthy devices get jobs
        self.features: list[str] = []

        self.poller: JobPoller | None = None
        self.executor: TestExecutor | None = None
        self._api_client: httpx.AsyncClient | None = None
        self._labgrid_client: LabgridClient | None = None
        self._discovery_manager: DeviceDiscoveryManager | None = None
        self._running = False
        self._health_check_task: asyncio.Task | None = None
        self._discovery_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """Initialize the adapter."""
        logger.info(f"Initializing Labgrid KCI Adapter for lab: {self.lab_name}")

        # Initialize labgrid client first (needed for discovery)
        self._labgrid_client = LabgridClient()

        # Discover devices from coordinator
        self._discovery_manager = DeviceDiscoveryManager(
            labgrid_client=self._labgrid_client,
            targets_dir=settings.targets_dir,
            refresh_interval=settings.device_discovery_interval,
            require_target_files=settings.require_target_files,
        )
        devices = await self._discovery_manager.discover()
        self.devices = list(devices.keys())
        self.features = self._discovery_manager.get_all_features()
        logger.info(f"Discovered {len(self.devices)} device types from coordinator")

        # Initialize API client
        self._api_client = httpx.AsyncClient(
            base_url=settings.kci_api_url,
            headers={
                "Authorization": f"Bearer {settings.kci_api_token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )

        # Initialize executor
        self.executor = TestExecutor(
            lab_name=self.lab_name,
            targets_dir=settings.targets_dir,
            tests_dir=settings.tests_dir,
        )
        await self.executor.initialize()

        # Initially assume all devices are healthy (health check will verify)
        self.healthy_devices = set(self.devices)

        # Initialize poller with healthy devices only
        # Poller uses labgrid client to support parallel execution
        # when multiple physical devices of same type are available
        self.poller = JobPoller(
            lab_name=self.lab_name,
            devices=list(self.healthy_devices),
            features=self.features,
            on_job=self._handle_job,
            labgrid_client=self._labgrid_client,
        )
        await self.poller.connect()

    async def shutdown(self) -> None:
        """Shutdown the adapter."""
        logger.info("Shutting down Labgrid KCI Adapter")
        self._running = False

        if self.poller:
            self.poller.stop()

        if self.executor:
            await self.executor.cleanup()

        if self._api_client:
            await self._api_client.aclose()

    async def _handle_job(self, job: dict) -> None:
        """
        Handle a job received from the poller.

        Args:
            job: Job definition from KernelCI API
        """
        job_id = job.get("id")
        logger.info(f"Handling job: {job_id}")

        try:
            # Execute the job
            result = await self.executor.execute_job(job)

            # Submit results
            await self._submit_results(result)

            logger.info(
                "Job completed",
                job_id=job_id,
                status=result.status,
                passed=result.passed_tests,
                failed=result.failed_tests,
            )

        except Exception as e:
            logger.exception(f"Error handling job {job_id}: {e}")

            # Try to mark job as failed
            try:
                await self._mark_job_failed(job_id, str(e))
            except Exception:
                logger.exception(f"Failed to mark job {job_id} as failed")

    async def _submit_results(self, result: JobResult) -> None:
        """Submit job results to KernelCI API."""
        logger.info(f"Submitting results for job: {result.job_id}")

        try:
            # Get the current node
            response = await self._api_client.get(f"/latest/node/{result.job_id}")
            response.raise_for_status()
            node = response.json()

            # Update node with results
            node["state"] = "done"
            node["result"] = result.status.value if hasattr(result.status, 'value') else result.status
            node_data = node.get("data", {})
            node_data["completed_at"] = result.completed_at.isoformat() if result.completed_at else None
            node_data["duration"] = result.duration
            node_data["test_results"] = [t.model_dump(mode="json") for t in result.test_results]
            if result.console_log_url:
                node_data["log_url"] = result.console_log_url
            node["data"] = node_data

            # PUT the updated node
            response = await self._api_client.put(
                f"/latest/node/{result.job_id}",
                json=node,
            )
            response.raise_for_status()
            logger.info(f"Results submitted for job: {result.job_id}")

        except Exception as e:
            logger.error(f"Failed to submit results: {e}")
            raise

    async def _mark_job_failed(self, job_id: str, error: str) -> None:
        """Mark a job as failed."""
        try:
            # Get the current node
            response = await self._api_client.get(f"/latest/node/{job_id}")
            response.raise_for_status()
            node = response.json()

            # Update node with failure
            node["state"] = "done"
            node["result"] = "fail"
            node_data = node.get("data", {})
            node_data["error_message"] = error
            node["data"] = node_data

            # PUT the updated node
            response = await self._api_client.put(
                f"/latest/node/{job_id}",
                json=node,
            )
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to mark job as failed: {e}")

    # =========================================================================
    # Health Check
    # =========================================================================

    async def _run_health_checks(self) -> None:
        """Run health checks on all devices and update healthy_devices set."""
        logger.info("Starting health check for all devices")

        for device in self.devices:
            target_file = settings.targets_dir / f"{device}.yaml"
            ok, message = await self._check_device_health(device, target_file)

            if ok:
                if device not in self.healthy_devices:
                    logger.info(f"Device {device} is now healthy")
                    self.healthy_devices.add(device)
                    # Update poller with new device list
                    if self.poller:
                        self.poller.devices = list(self.healthy_devices)
            else:
                if device in self.healthy_devices:
                    logger.warning(f"Device {device} failed health check: {message}")
                    self.healthy_devices.discard(device)
                    # Update poller to stop accepting jobs for this device
                    if self.poller:
                        self.poller.devices = list(self.healthy_devices)
                else:
                    logger.warning(f"Device {device} still unhealthy: {message}")

        logger.info(
            f"Health check complete: {len(self.healthy_devices)}/{len(self.devices)} "
            "devices healthy"
        )

    async def _check_device_health(
        self, device: str, target_file: os.PathLike
    ) -> tuple[bool, str]:
        """
        Check if a device is accessible via labgrid coordinator.

        Uses place-based acquisition (labgrid-client -p <place>) instead of
        config file-based, since target files require template variables
        (LG_IMAGE, etc.) that are only available at job execution time.

        Returns:
            Tuple of (is_healthy, message)
        """
        try:
            env = os.environ.copy()
            env["LG_COORDINATOR"] = settings.lg_coordinator

            # Construct place name from lab name and device
            # Place naming convention: {lab_name}-{device_type}
            # Lab name already includes full prefix (e.g., "labgrid-aparcar")
            place_name = f"{self.lab_name}-{device}"

            # Try to acquire the place
            proc = await asyncio.create_subprocess_exec(
                "labgrid-client",
                "-p",
                place_name,
                "acquire",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return (False, "Timeout acquiring device")

            if proc.returncode != 0:
                return (False, f"Acquire failed: {stderr.decode().strip()}")

            # Release immediately
            release_proc = await asyncio.create_subprocess_exec(
                "labgrid-client",
                "-p",
                place_name,
                "release",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(release_proc.communicate(), timeout=10)

            return (True, "OK")

        except Exception as e:
            return (False, str(e))

    async def _health_check_loop(self) -> None:
        """Background task that runs health checks periodically."""
        # Run initial health check
        await self._run_health_checks()

        while self._running:
            try:
                await asyncio.sleep(settings.health_check_interval)
                if self._running:
                    await self._run_health_checks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in health check loop: {e}")
                # Continue running despite errors
                await asyncio.sleep(60)

    async def _discovery_refresh_loop(self) -> None:
        """Background task that refreshes device discovery periodically."""
        while self._running:
            try:
                await asyncio.sleep(settings.device_discovery_interval)
                if self._running and self._discovery_manager:
                    old_devices = set(self.devices)
                    devices = await self._discovery_manager.discover(force_refresh=True)
                    new_devices = set(devices.keys())

                    # Log changes
                    added = new_devices - old_devices
                    removed = old_devices - new_devices

                    if added:
                        logger.info(f"New devices discovered: {added}")
                    if removed:
                        logger.info(f"Devices removed: {removed}")

                    # Update device list
                    self.devices = list(new_devices)
                    self.features = self._discovery_manager.get_all_features()

                    # Update healthy devices (remove any that no longer exist)
                    self.healthy_devices &= new_devices

                    # Update poller device list
                    if self.poller:
                        self.poller.devices = [
                            d for d in self.devices if d in self.healthy_devices
                        ]

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in discovery refresh loop: {e}")
                await asyncio.sleep(60)

    async def run(self) -> None:
        """Main service loop."""
        self._running = True
        logger.info("Starting Labgrid KCI Adapter")

        try:
            # Start discovery refresh loop in background
            if self._discovery_manager:
                self._discovery_task = asyncio.create_task(
                    self._discovery_refresh_loop()
                )
                logger.info(
                    f"Device discovery refresh interval: "
                    f"{settings.device_discovery_interval}s"
                )

            # Start health check loop in background
            if settings.health_check_enabled:
                self._health_check_task = asyncio.create_task(self._health_check_loop())
                logger.info(
                    f"Health checks enabled, interval: "
                    f"{settings.health_check_interval}s"
                )

            # Start the poller (uses healthy_devices)
            # Note: Tests are pulled before each job execution, not in background
            await self.poller.run()
        except asyncio.CancelledError:
            logger.info("Adapter cancelled")
        except Exception as e:
            logger.exception(f"Adapter error: {e}")
        finally:
            if self._discovery_task:
                self._discovery_task.cancel()
                try:
                    await self._discovery_task
                except asyncio.CancelledError:
                    pass
            if self._health_check_task:
                self._health_check_task.cancel()
                try:
                    await self._health_check_task
                except asyncio.CancelledError:
                    pass
            await self.shutdown()


# =============================================================================
# Main Entry Point
# =============================================================================


async def main():
    """Main entry point."""
    adapter = LabgridKCIAdapter()

    # Handle signals
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        loop.create_task(adapter.shutdown())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await adapter.initialize()
        await adapter.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    finally:
        await adapter.shutdown()


def run():
    """Synchronous entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
