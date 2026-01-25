"""
Test Scheduler Service

Schedules test jobs for firmware based on:
- Device compatibility (target/subtarget/profile)
- Device features (wifi, wan_port, etc.)
- Job priority
- Lab availability

This service runs continuously and:
1. Listens for new firmware events
2. Creates test jobs for compatible devices
3. Monitors job progress and handles failures
"""

import asyncio

import structlog

from .api_client import APIError, KernelCIClient
from .config import load_pipeline_config
from .models import JobCreate

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


class TestScheduler:
    """
    Schedules test jobs based on firmware and device compatibility.
    """

    def __init__(self):
        self.config = load_pipeline_config()
        self.api_client: KernelCIClient | None = None
        self._running = False

    async def initialize(self) -> None:
        """Initialize the scheduler."""
        logger.info("Initializing Test Scheduler")

        self.api_client = KernelCIClient()
        await self.api_client.connect()

    async def shutdown(self) -> None:
        """Cleanup resources."""
        logger.info("Shutting down Test Scheduler")
        self._running = False

        if self.api_client:
            await self.api_client.close()

    async def run(self) -> None:
        """Main scheduler loop."""
        self._running = True
        logger.info("Starting Test Scheduler")

        # Run concurrent tasks
        await asyncio.gather(
            self._event_listener(),
            self._job_monitor(),
            return_exceptions=True,
        )

    async def _event_listener(self) -> None:
        """
        Listen for firmware events and create jobs.

        In a real implementation, this would use WebSocket or SSE
        to receive events from the API. For now, we poll.
        """
        logger.info("Starting event listener")

        while self._running:
            try:
                # Get recent firmware that needs jobs
                firmware_list = await self.api_client.list_firmware(
                    limit=50,
                )

                for firmware in firmware_list:
                    # Check if jobs already exist for this firmware
                    existing_jobs = await self.api_client.get_results(
                        firmware_id=firmware.id,
                        limit=1,
                    )

                    if not existing_jobs:
                        # Create jobs for this firmware
                        await self._create_jobs_for_firmware(firmware)

            except Exception as e:
                logger.exception(f"Error in event listener: {e}")

            await asyncio.sleep(30)

    async def _job_monitor(self) -> None:
        """
        Monitor running jobs and handle timeouts.
        """
        logger.info("Starting job monitor")

        while self._running:
            try:
                # This would monitor for stuck/timed out jobs
                # and retry or mark as failed
                pass

            except Exception as e:
                logger.exception(f"Error in job monitor: {e}")

            await asyncio.sleep(60)

    async def _create_jobs_for_firmware(self, firmware) -> None:
        """
        Create test jobs for a firmware image.

        Finds compatible devices and creates jobs with appropriate
        test plans based on device features.
        """
        logger.info(
            "Creating jobs for firmware",
            firmware_id=firmware.id,
            target=firmware.target,
            profile=firmware.profile,
        )

        device_types = self.config.get("device_types", {})
        scheduler_config = self.config.get("scheduler", {})
        test_plans_config = self.config.get("test_plans", {})

        # Find compatible devices
        compatible_devices = self._find_compatible_devices(
            firmware.target,
            firmware.subtarget,
            firmware.profile,
            device_types,
        )

        if not compatible_devices:
            logger.warning(
                "No compatible devices for firmware",
                firmware_id=firmware.id,
                target=firmware.target,
            )
            return

        # Determine priority based on source
        source_priorities = scheduler_config.get("priorities", {})
        priority = source_priorities.get(firmware.source.value, 5)

        # Create jobs for each compatible device
        for device_name, device_config in compatible_devices.items():
            # Get test plans for this device
            test_plans = self._get_test_plans_for_device(
                device_config,
                firmware.source.value,
                scheduler_config,
            )

            for plan_name in test_plans:
                plan_config = test_plans_config.get(plan_name, {})

                # Check if device has required features
                required_features = plan_config.get("required_features", [])
                device_features = device_config.get("features", [])

                if not all(f in device_features for f in required_features):
                    logger.debug(
                        f"Skipping {plan_name} for {device_name}: missing features",
                        required=required_features,
                        available=device_features,
                    )
                    continue

                # Create the job
                try:
                    job = JobCreate(
                        firmware_id=firmware.id,
                        device_type=device_name,
                        test_plan=plan_name,
                        tests=plan_config.get("tests", []),
                        priority=priority,
                        timeout=plan_config.get("timeout", 1800),
                        skip_firmware_flash=plan_config.get(
                            "skip_firmware_flash", False
                        ),
                    )

                    created = await self.api_client.create_job(job)
                    logger.info(
                        "Created job",
                        job_id=created.id,
                        device=device_name,
                        test_plan=plan_name,
                    )

                except APIError as e:
                    if e.status_code == 409:
                        logger.debug(
                            f"Job already exists for {device_name}/{plan_name}"
                        )
                    else:
                        logger.error(f"Failed to create job: {e}")
                except Exception as e:
                    logger.exception(f"Error creating job: {e}")

    def _find_compatible_devices(
        self,
        target: str,
        subtarget: str,
        profile: str,
        device_types: dict,
    ) -> dict[str, dict]:
        """
        Find devices compatible with a firmware.

        Matches based on target/subtarget, and optionally profile.
        """
        compatible = {}

        for device_name, device_config in device_types.items():
            device_target = device_config.get("target")
            device_subtarget = device_config.get("subtarget")
            device_profile = device_config.get("profile")

            # Must match target and subtarget
            if device_target != target or device_subtarget != subtarget:
                continue

            # Profile matching (wildcard * matches any)
            if profile != "*" and device_profile and device_profile != profile:
                continue

            compatible[device_name] = device_config

        return compatible

    def _get_test_plans_for_device(
        self,
        device_config: dict,
        firmware_source: str,
        scheduler_config: dict,
    ) -> list[str]:
        """
        Determine which test plans to run for a device.

        Based on:
        - Default plans for the firmware source
        - Additional plans based on device features
        """
        plans = []

        # Default plans for firmware source
        default_plans = scheduler_config.get("default_test_plans", {})
        plans.extend(default_plans.get(firmware_source, ["base"]))

        # Feature-based plans
        feature_plans = scheduler_config.get("feature_test_plans", {})
        device_features = device_config.get("features", [])

        for feature in device_features:
            if feature in feature_plans:
                plans.extend(feature_plans[feature])

        # Remove duplicates while preserving order
        seen = set()
        unique_plans = []
        for plan in plans:
            if plan not in seen:
                seen.add(plan)
                unique_plans.append(plan)

        return unique_plans


# =============================================================================
# Main Entry Point
# =============================================================================


async def main():
    """Main entry point."""
    scheduler = TestScheduler()

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
