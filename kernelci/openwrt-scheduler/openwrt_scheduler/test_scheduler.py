"""
Test Scheduler Service

Schedules test jobs for firmware based on:
- Device compatibility (target/subtarget/profile)
- Device features (wifi, wan_port, etc.)
- Test type (firmware tests vs kernel selftests)
- Job priority
- Lab availability

This service runs continuously and:
1. Listens for new firmware events
2. Creates test jobs for compatible devices
3. Builds custom images when needed (via ASU)
4. Monitors job progress and handles failures
"""

import asyncio
import logging
import sys

import structlog

from .api_client import APIError, KernelCIClient
from .asu_client import ASUClient, ImageBuildRequest
from .config import load_pipeline_config
from .test_types import (
    TEST_TYPE_CONFIGS,
    TestType,
    device_supports_test_type,
    get_image_profile,
    get_test_type_config,
    needs_custom_image,
)

# Configure stdlib logging first (required for structlog.stdlib)
logging.basicConfig(
    format="%(message)s",
    stream=sys.stdout,
    level=logging.INFO,
)

# Configure structlog
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

    Supports multiple test types:
    - firmware: Standard OpenWrt functionality tests
    - kselftest: Linux kernel validation tests (requires custom images)
    """

    def __init__(self):
        self.config = load_pipeline_config()
        self.api_client: KernelCIClient | None = None
        self.asu_client: ASUClient | None = None
        self._running = False
        # Cache for custom image URLs: (target, subtarget, profile, version, test_type) -> url
        self._image_cache: dict[tuple, str] = {}

    async def initialize(self) -> None:
        """Initialize the scheduler."""
        logger.info("Initializing Test Scheduler")

        self.api_client = KernelCIClient()
        await self.api_client.connect()

        self.asu_client = ASUClient()
        await self.asu_client.connect()

    async def shutdown(self) -> None:
        """Cleanup resources."""
        logger.info("Shutting down Test Scheduler")
        self._running = False

        if self.api_client:
            await self.api_client.close()

        if self.asu_client:
            await self.asu_client.close()

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

        Uses the Node-based API to query for firmware (kbuild nodes)
        that don't yet have test jobs scheduled.
        """
        logger.info("Starting event listener")

        while self._running:
            try:
                # Get recent firmware nodes (kind=kbuild, state=available)
                firmware_nodes = await self.api_client.query_nodes(
                    kind="kbuild",
                    state="available",
                    limit=50,
                )

                for firmware_node in firmware_nodes:
                    firmware_id = firmware_node.get("id") or firmware_node.get("_id")

                    # Check if jobs already exist for this firmware
                    existing_jobs = await self.api_client.query_nodes(
                        kind="job",
                        parent=firmware_id,
                        limit=1,
                    )

                    if not existing_jobs:
                        # Create jobs for this firmware
                        await self._create_jobs_for_firmware(firmware_node)

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

    async def _create_jobs_for_firmware(self, firmware_node: dict) -> None:
        """
        Create test jobs for a firmware image.

        Finds compatible devices and creates jobs with appropriate
        test plans based on device features and test types.

        Creates jobs for multiple test types:
        - firmware: Standard tests using official image
        - kselftest: Kernel tests using custom image with test packages

        Args:
            firmware_node: Firmware node dict from KernelCI API
        """
        firmware_id = firmware_node.get("id") or firmware_node.get("_id")
        firmware_data = firmware_node.get("data", {})
        target = firmware_data.get("target", "")
        subtarget = firmware_data.get("subtarget", "")
        profile = firmware_data.get("profile", "*")
        version = firmware_data.get("openwrt_version", "SNAPSHOT")
        source = firmware_data.get("source", "official")
        artifacts = firmware_data.get("artifacts", {})

        logger.info(
            "Creating jobs for firmware",
            firmware_id=firmware_id,
            target=target,
            profile=profile,
            version=version,
        )

        device_types = self.config.get("device_types", {})
        scheduler_config = self.config.get("scheduler", {})

        # Find compatible devices
        compatible_devices = self._find_compatible_devices(
            target,
            subtarget,
            profile,
            device_types,
        )

        if not compatible_devices:
            logger.warning(
                "No compatible devices for firmware",
                firmware_id=firmware_id,
                target=target,
            )
            return

        # Get enabled test types from config
        enabled_test_types = scheduler_config.get(
            "enabled_test_types", ["firmware"]
        )

        # Create jobs for each test type
        for test_type_str in enabled_test_types:
            try:
                test_type = TestType(test_type_str)
            except ValueError:
                logger.warning(f"Unknown test type: {test_type_str}")
                continue

            await self._create_jobs_for_test_type(
                firmware_id=firmware_id,
                firmware_data=firmware_data,
                test_type=test_type,
                compatible_devices=compatible_devices,
                artifacts=artifacts,
            )

    async def _create_jobs_for_test_type(
        self,
        firmware_id: str,
        firmware_data: dict,
        test_type: TestType,
        compatible_devices: dict[str, dict],
        artifacts: dict[str, str],
    ) -> None:
        """
        Create jobs for a specific test type.

        Args:
            firmware_id: Parent firmware node ID
            firmware_data: Firmware metadata
            test_type: Type of tests to create jobs for
            compatible_devices: Devices that can run the firmware
            artifacts: Firmware artifact URLs
        """
        test_type_config = get_test_type_config(test_type)
        if not test_type_config:
            logger.warning(f"No config for test type: {test_type}")
            return

        target = firmware_data.get("target", "")
        subtarget = firmware_data.get("subtarget", "")
        profile = firmware_data.get("profile", "")
        version = firmware_data.get("openwrt_version", "SNAPSHOT")

        # Determine firmware URL for this test type
        if needs_custom_image(test_type):
            # Build custom image with required packages
            firmware_url = await self._get_custom_image_url(
                target=target,
                subtarget=subtarget,
                profile=profile,
                version=version,
                test_type=test_type,
            )
            if not firmware_url:
                logger.warning(
                    f"Failed to get custom image for {test_type}, skipping"
                )
                return
        else:
            # Use standard firmware image
            firmware_url = artifacts.get("sysupgrade") or artifacts.get("factory")

        # Create jobs for compatible devices that support this test type
        for device_name, device_config in compatible_devices.items():
            device_capabilities = device_config.get("capabilities", [])

            # Check if device supports this test type
            if not device_supports_test_type(device_capabilities, test_type):
                logger.debug(
                    f"Device {device_name} doesn't support {test_type.value}",
                    capabilities=device_capabilities,
                    required=test_type_config.required_capabilities,
                )
                continue

            # Create jobs for each test plan in this test type
            for plan_name in test_type_config.test_plans:
                try:
                    created = await self.api_client.create_test_job(
                        firmware_node_id=firmware_id,
                        device_type=device_name,
                        test_plan=plan_name,
                        test_type=test_type.value,
                        firmware_url=firmware_url,
                        tests_subdir=test_type_config.tests_subdir,
                        timeout=1800,
                    )
                    job_id = created.get("id") or created.get("_id")
                    logger.info(
                        "Created job",
                        job_id=job_id,
                        device=device_name,
                        test_type=test_type.value,
                        test_plan=plan_name,
                    )

                except APIError as e:
                    if e.status_code == 409:
                        logger.debug(
                            f"Job already exists: {device_name}/{test_type.value}/{plan_name}"
                        )
                    else:
                        logger.error(f"Failed to create job: {e}")
                except Exception as e:
                    logger.exception(f"Error creating job: {e}")

    async def _get_custom_image_url(
        self,
        target: str,
        subtarget: str,
        profile: str,
        version: str,
        test_type: TestType,
    ) -> str | None:
        """
        Get URL for a custom image with packages for a test type.

        Uses ASU to build the image if not cached.

        Args:
            target: Hardware target
            subtarget: Subtarget
            profile: Device profile
            version: OpenWrt version
            test_type: Test type requiring custom packages

        Returns:
            URL to the custom sysupgrade image, or None on failure
        """
        cache_key = (target, subtarget, profile, version, test_type.value)

        # Check cache first
        if cache_key in self._image_cache:
            logger.debug(f"Using cached custom image for {cache_key}")
            return self._image_cache[cache_key]

        # Get packages for this test type
        test_type_config = get_test_type_config(test_type)
        if not test_type_config:
            return None

        image_profile = get_image_profile(test_type_config.image_profile)
        if not image_profile:
            return None

        packages = image_profile.packages

        logger.info(
            f"Building custom image for {test_type.value}",
            target=target,
            profile=profile,
            packages=packages,
        )

        try:
            request = ImageBuildRequest(
                target=target,
                subtarget=subtarget,
                profile=profile,
                version=version,
                packages=packages,
            )

            result = await self.asu_client.build_and_wait(request)

            if result.sysupgrade_url:
                self._image_cache[cache_key] = result.sysupgrade_url
                return result.sysupgrade_url
            elif result.factory_url:
                self._image_cache[cache_key] = result.factory_url
                return result.factory_url
            else:
                logger.error(f"No image URL in build result: {result}")
                return None

        except Exception as e:
            logger.exception(f"Failed to build custom image: {e}")
            return None

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
