"""
Health Check Scheduler

Periodically schedules and monitors device health checks:
- Identifies devices needing checks
- Creates health check jobs
- Processes results and updates device status
- Triggers notifications for failures
"""

import asyncio

import structlog

from ..api_client import KernelCIClient
from ..config import load_pipeline_config
from ..models import JobCreate
from .device_registry import DeviceRegistry
from .notifications import NotificationManager

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


class HealthCheckScheduler:
    """
    Schedules and monitors device health checks.

    The scheduler:
    1. Identifies devices due for health checks
    2. Creates high-priority health check jobs
    3. Monitors job completion
    4. Updates device status based on results
    5. Triggers notifications for failures/recoveries
    """

    def __init__(self):
        self.config = load_pipeline_config()
        self.health_config = self.config.get("health_check", {})

        self.api_client: KernelCIClient | None = None
        self.device_registry: DeviceRegistry | None = None
        self.notifications: NotificationManager | None = None

        self._running = False
        self._pending_checks: dict[str, str] = {}  # device_id -> job_id

    async def initialize(self) -> None:
        """Initialize the health check scheduler."""
        logger.info("Initializing Health Check Scheduler")

        # Initialize API client
        self.api_client = KernelCIClient()
        await self.api_client.connect()

        # Initialize device registry
        self.device_registry = DeviceRegistry(
            api_client=self.api_client,
            config=self.health_config,
        )
        await self.device_registry.initialize()

        # Initialize notifications
        notification_config = self.health_config.get("notifications", {})
        self.notifications = NotificationManager(notification_config)
        self.notifications.initialize()

        logger.info(
            "Health scheduler initialized",
            devices=len(self.device_registry.get_all_devices()),
            interval=self.health_config.get("interval", 86400),
        )

    async def shutdown(self) -> None:
        """Cleanup resources."""
        logger.info("Shutting down Health Check Scheduler")
        self._running = False

        if self.notifications:
            self.notifications.cleanup()

        if self.api_client:
            await self.api_client.close()

    async def run(self) -> None:
        """Main scheduler loop."""
        self._running = True
        logger.info("Starting Health Check Scheduler")

        if not self.health_config.get("enabled", True):
            logger.info("Health checks disabled in configuration")
            return

        # Run scheduler and result monitor concurrently
        await asyncio.gather(
            self._schedule_loop(),
            self._monitor_loop(),
            return_exceptions=True,
        )

    async def _schedule_loop(self) -> None:
        """
        Periodically check for devices needing health checks.
        """
        check_frequency = 3600  # Check every hour for devices needing checks

        while self._running:
            try:
                await self._schedule_pending_checks()
            except Exception as e:
                logger.exception(f"Error in schedule loop: {e}")

            await asyncio.sleep(check_frequency)

    async def _monitor_loop(self) -> None:
        """
        Monitor pending health check jobs for completion.
        """
        monitor_frequency = 60  # Check every minute

        while self._running:
            try:
                await self._process_completed_checks()
            except Exception as e:
                logger.exception(f"Error in monitor loop: {e}")

            await asyncio.sleep(monitor_frequency)

    async def _schedule_pending_checks(self) -> None:
        """
        Find devices needing checks and create jobs.
        """
        devices = self.device_registry.get_devices_needing_check()

        if not devices:
            logger.debug("No devices need health checks")
            return

        logger.info(f"Scheduling health checks for {len(devices)} devices")

        test_plan = self.health_config.get("test_plan", "health_check")
        tests = ["test_shell", "test_ssh"]  # Minimal health check tests

        for device in devices:
            # Skip if already pending
            if device.id in self._pending_checks:
                continue

            try:
                job = JobCreate(
                    firmware_id="health_check",  # Special ID for health checks
                    device_type=device.id,
                    test_plan=test_plan,
                    tests=tests,
                    priority=10,  # Highest priority
                    timeout=300,  # 5 minute timeout
                    skip_firmware_flash=True,  # Don't flash firmware
                )

                created = await self.api_client.create_job(job)

                # Track pending check
                self._pending_checks[device.id] = created.id

                logger.info(
                    "Scheduled health check",
                    device_id=device.id,
                    job_id=created.id,
                )

            except Exception as e:
                logger.error(f"Failed to schedule health check for {device.id}: {e}")

    async def _process_completed_checks(self) -> None:
        """
        Process results from completed health check jobs.
        """
        if not self._pending_checks:
            return

        completed = []

        for device_id, job_id in self._pending_checks.items():
            try:
                job = await self.api_client.get_job(job_id)

                if job is None:
                    logger.warning(f"Health check job not found: {job_id}")
                    completed.append(device_id)
                    continue

                # Check if job is complete
                if job.status not in ("complete", "failed", "timeout"):
                    continue

                # Process the result
                await self._process_health_result(device_id, job)
                completed.append(device_id)

            except Exception as e:
                logger.error(f"Error processing health check for {device_id}: {e}")

        # Remove completed checks
        for device_id in completed:
            self._pending_checks.pop(device_id, None)

    async def _process_health_result(self, device_id: str, job) -> None:
        """
        Process a completed health check job.

        Updates device status and triggers notifications.
        """
        logger.info(
            "Processing health check result",
            device_id=device_id,
            job_id=job.id,
            status=job.status,
        )

        device = self.device_registry.get_device(device_id)
        if not device:
            logger.warning(f"Device not found: {device_id}")
            return

        previous_status = device.status

        # Determine if check passed
        passed = job.status == "complete"

        if passed:
            # Mark device as healthy
            updated = await self.device_registry.mark_healthy(device_id)

            # Notify recovery if was failing/disabled
            if previous_status in ("failing", "disabled"):
                await self.notifications.notify_device_recovery(updated)

        else:
            # Record failure
            updated = await self.device_registry.record_failure(
                device_id,
                error=getattr(job, "error_message", None),
            )

            # Notify if newly disabled
            if updated.status == "disabled" and previous_status != "disabled":
                await self.notifications.notify_device_failure(
                    updated,
                    error_message=getattr(job, "error_message", None),
                    console_log_url=getattr(job, "console_log_url", None),
                )

    async def trigger_health_check(self, device_id: str) -> str:
        """
        Manually trigger a health check for a device.

        Args:
            device_id: Device to check

        Returns:
            Job ID for the health check
        """
        device = self.device_registry.get_device(device_id)
        if not device:
            raise ValueError(f"Unknown device: {device_id}")

        test_plan = self.health_config.get("test_plan", "health_check")

        job = JobCreate(
            firmware_id="health_check",
            device_type=device_id,
            test_plan=test_plan,
            tests=["test_shell", "test_ssh"],
            priority=10,
            timeout=300,
            skip_firmware_flash=True,
        )

        created = await self.api_client.create_job(job)
        self._pending_checks[device_id] = created.id

        logger.info(
            "Manual health check triggered",
            device_id=device_id,
            job_id=created.id,
        )

        return created.id

    def get_status(self) -> dict:
        """Get current health check status."""
        summary = self.device_registry.get_health_summary()

        return {
            "enabled": self.health_config.get("enabled", True),
            "interval": self.health_config.get("interval", 86400),
            "pending_checks": len(self._pending_checks),
            "summary": summary,
        }


# =============================================================================
# Main Entry Point
# =============================================================================


async def main():
    """Main entry point."""
    scheduler = HealthCheckScheduler()

    try:
        await scheduler.initialize()
        await scheduler.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    finally:
        await scheduler.shutdown()


def run():
    """Synchronous entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
