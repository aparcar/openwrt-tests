"""
Test Executor for Labgrid

Executes test jobs using labgrid for device control and pytest
for test execution. Handles:
- Firmware downloading and flashing
- Test execution with proper isolation
- Result collection and formatting
- Console log capture
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

import httpx
from minio import Minio

from .config import settings
from .models import JobResult, TestResult, TestStatus

logger = logging.getLogger(__name__)


class TestExecutor:
    """
    Executes test jobs using labgrid and pytest.

    The executor:
    1. Downloads firmware artifacts
    2. Acquires the labgrid target
    3. Flashes firmware (if needed)
    4. Runs pytest with the specified tests
    5. Collects results and logs
    6. Releases the target
    """

    def __init__(self, lab_name: str, targets_dir: Path, tests_dir: Path):
        """
        Initialize the test executor.

        Args:
            lab_name: Name of this lab
            targets_dir: Directory containing labgrid target YAML files
            tests_dir: Directory containing pytest test files
        """
        self.lab_name = lab_name
        self.targets_dir = targets_dir
        self.tests_dir = tests_dir
        self.cache_dir = Path(settings.firmware_cache)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._http_client: httpx.AsyncClient | None = None
        self._minio: Minio | None = None

    async def initialize(self) -> None:
        """Initialize HTTP client and storage client."""
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),  # 5 minutes for firmware download
            follow_redirects=True,
        )

        if settings.minio_endpoint:
            self._minio = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self._http_client:
            await self._http_client.aclose()

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Get HTTP client."""
        if self._http_client is None:
            raise RuntimeError("Executor not initialized")
        return self._http_client

    async def execute_job(self, job: dict) -> JobResult:
        """
        Execute a test job.

        Args:
            job: Job definition from KernelCI API

        Returns:
            JobResult with test results
        """
        job_id = job["id"]
        firmware_id = job.get("firmware_id")
        device_type = job["device_type"]
        test_plan = job.get("test_plan", "base")
        tests = job.get("tests", [])
        skip_flash = job.get("skip_firmware_flash", False)
        timeout = job.get("timeout", 1800)

        logger.info(
            f"Executing job {job_id}",
            extra={
                "device": device_type,
                "firmware": firmware_id,
                "test_plan": test_plan,
            },
        )

        start_time = datetime.utcnow()
        test_results: list[TestResult] = []
        console_log_path: Path | None = None

        try:
            # Create temporary directory for this job
            with tempfile.TemporaryDirectory(prefix=f"job-{job_id}-") as tmpdir:
                tmpdir_path = Path(tmpdir)
                console_log_path = tmpdir_path / "console.log"

                # Download firmware if needed
                firmware_path = None
                if not skip_flash and firmware_id:
                    firmware_info = job.get("firmware", {})
                    firmware_path = await self._download_firmware(
                        firmware_id=firmware_id,
                        firmware_info=firmware_info,
                        dest_dir=tmpdir_path,
                    )

                # Build pytest command
                pytest_args = self._build_pytest_args(
                    device_type=device_type,
                    tests=tests,
                    firmware_path=firmware_path,
                    results_dir=tmpdir_path,
                    skip_flash=skip_flash,
                )

                # Run pytest
                await self._run_pytest(
                    pytest_args=pytest_args,
                    timeout=timeout,
                    console_log=console_log_path,
                )

                # Parse results
                results_file = tmpdir_path / "results.json"
                if results_file.exists():
                    test_results = self._parse_results(
                        results_file=results_file,
                        job_id=job_id,
                        firmware_id=firmware_id or "",
                        device_type=device_type,
                    )

                # Upload console log
                console_log_url = None
                if console_log_path.exists():
                    console_log_url = await self._upload_log(
                        log_path=console_log_path,
                        job_id=job_id,
                    )

        except Exception as e:
            logger.exception(f"Job {job_id} failed with error: {e}")
            # Create error result
            test_results = [
                TestResult(
                    id=f"{job_id}:error",
                    job_id=job_id,
                    firmware_id=firmware_id or "",
                    device_type=device_type,
                    lab_name=self.lab_name,
                    test_name="job_execution",
                    status=TestStatus.ERROR,
                    duration=0,
                    start_time=start_time,
                    error_message=str(e),
                )
            ]

        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()

        # Calculate summary
        passed = sum(1 for r in test_results if r.status == TestStatus.PASS)
        failed = sum(1 for r in test_results if r.status == TestStatus.FAIL)
        skipped = sum(1 for r in test_results if r.status == TestStatus.SKIP)
        errors = sum(1 for r in test_results if r.status == TestStatus.ERROR)

        return JobResult(
            job_id=job_id,
            firmware_id=firmware_id or "",
            device_type=device_type,
            lab_name=self.lab_name,
            status="complete" if errors == 0 and failed == 0 else "failed",
            total_tests=len(test_results),
            passed_tests=passed,
            failed_tests=failed,
            skipped_tests=skipped,
            error_tests=errors,
            started_at=start_time,
            completed_at=end_time,
            duration=duration,
            test_results=test_results,
            console_log_url=console_log_url,
        )

    async def _download_firmware(
        self,
        firmware_id: str,
        firmware_info: dict,
        dest_dir: Path,
    ) -> Path | None:
        """Download firmware to local cache."""
        artifacts = firmware_info.get("artifacts", {})

        # Prefer initramfs for testing, then sysupgrade
        for artifact_type in ["initramfs", "sysupgrade", "factory"]:
            url = artifacts.get(artifact_type)
            if not url:
                continue

            # Check cache
            cache_key = f"{firmware_id}_{artifact_type}"
            cache_path = self.cache_dir / cache_key
            if cache_path.exists():
                logger.info(f"Using cached firmware: {cache_path}")
                return cache_path

            # Download
            logger.info(f"Downloading firmware: {url}")
            try:
                response = await self.http_client.get(url)
                response.raise_for_status()

                # Save to cache
                cache_path.write_bytes(response.content)
                logger.info(f"Firmware cached: {cache_path}")
                return cache_path

            except Exception as e:
                logger.warning(f"Failed to download {artifact_type}: {e}")
                continue

        logger.warning(f"No firmware artifacts available for {firmware_id}")
        return None

    def _build_pytest_args(
        self,
        device_type: str,
        tests: list[str],
        firmware_path: Path | None,
        results_dir: Path,
        skip_flash: bool,
    ) -> list[str]:
        """Build pytest command arguments."""
        target_file = self.targets_dir / f"{device_type}.yaml"

        args = [
            "pytest",
            str(self.tests_dir),
            "-v",
            "--tb=short",
            f"--lg-env={target_file}",
            f"--junitxml={results_dir / 'junit.xml'}",
            "--json-report",
            f"--json-report-file={results_dir / 'results.json'}",
        ]

        # Add specific tests if provided
        if tests:
            for test in tests:
                args.extend(["-k", test])

        # Set firmware path in environment
        if firmware_path:
            args.extend(["--lg-firmware", str(firmware_path)])

        # Skip firmware flash if requested
        if skip_flash:
            args.append("--lg-skip-flash")

        return args

    async def _run_pytest(
        self,
        pytest_args: list[str],
        timeout: int,
        console_log: Path,
    ) -> int:
        """Run pytest and capture output."""
        logger.info(f"Running pytest: {' '.join(pytest_args)}")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["LG_CONSOLE"] = "internal"
        # Set labgrid coordinator address (gRPC)
        env["LG_COORDINATOR"] = settings.lg_coordinator

        with open(console_log, "w") as log_file:
            try:
                process = await asyncio.create_subprocess_exec(
                    *pytest_args,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                    env=env,
                )

                try:
                    returncode = await asyncio.wait_for(
                        process.wait(),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"pytest timed out after {timeout}s")
                    process.kill()
                    await process.wait()
                    returncode = -1

            except Exception as e:
                logger.exception(f"Error running pytest: {e}")
                returncode = -1

        logger.info(f"pytest completed with return code: {returncode}")
        return returncode

    def _parse_results(
        self,
        results_file: Path,
        job_id: str,
        firmware_id: str,
        device_type: str,
    ) -> list[TestResult]:
        """Parse pytest JSON results."""
        with open(results_file) as f:
            data = json.load(f)

        test_results = []
        tests = data.get("tests", [])

        for test in tests:
            nodeid = test.get("nodeid", "")
            outcome = test.get("outcome", "error")
            duration = test.get("duration", 0)

            # Map pytest outcome to TestStatus
            status_map = {
                "passed": TestStatus.PASS,
                "failed": TestStatus.FAIL,
                "skipped": TestStatus.SKIP,
                "error": TestStatus.ERROR,
            }
            status = status_map.get(outcome, TestStatus.ERROR)

            # Extract test name from nodeid
            test_name = nodeid.split("::")[-1] if "::" in nodeid else nodeid

            # Get error message if failed
            error_message = None
            if outcome in ("failed", "error"):
                call_info = test.get("call", {})
                error_message = call_info.get("longrepr", "")
                if isinstance(error_message, dict):
                    error_message = error_message.get("reprcrash", {}).get(
                        "message", ""
                    )

            result = TestResult(
                id=f"{job_id}:{test_name}",
                job_id=job_id,
                firmware_id=firmware_id,
                device_type=device_type,
                lab_name=self.lab_name,
                test_name=test_name,
                test_path=nodeid,
                status=status,
                duration=duration,
                start_time=datetime.utcnow(),  # Approximate
                error_message=error_message,
            )
            test_results.append(result)

        return test_results

    async def _upload_log(self, log_path: Path, job_id: str) -> str | None:
        """Upload console log to storage."""
        if not self._minio:
            logger.debug("MinIO not configured, skipping log upload")
            return None

        try:
            object_name = f"logs/{job_id}/console.log"
            self._minio.fput_object(
                bucket_name="openwrt-logs",
                object_name=object_name,
                file_path=str(log_path),
                content_type="text/plain",
            )

            url = f"http://{settings.minio_endpoint}/openwrt-logs/{object_name}"
            logger.info(f"Uploaded console log: {url}")
            return url

        except Exception as e:
            logger.warning(f"Failed to upload log: {e}")
            return None
