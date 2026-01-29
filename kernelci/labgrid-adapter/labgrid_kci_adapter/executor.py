"""
Test Executor for Labgrid

Executes test jobs using labgrid for device control and pytest
for test execution. Uses pytest's programmatic API for execution
and result collection.
"""

import io
import logging
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from minio import Minio

from .config import settings
from .models import JobResult, TestResult, TestStatus

logger = logging.getLogger(__name__)


class ResultCollectorPlugin:
    """
    Pytest plugin to collect test results programmatically.

    Captures test outcomes, durations, and error messages without
    requiring external JSON report files.
    """

    def __init__(self):
        self.results: list[dict] = []
        self.start_time: datetime | None = None
        self.end_time: datetime | None = None

    def pytest_sessionstart(self, session):
        self.start_time = datetime.utcnow()

    def pytest_sessionfinish(self, session, exitstatus):
        self.end_time = datetime.utcnow()

    def pytest_runtest_logreport(self, report):
        """Collect test results from each test phase."""
        # Only capture the 'call' phase (actual test execution)
        # Skip 'setup' and 'teardown' phases
        if report.when != "call":
            return

        result = {
            "nodeid": report.nodeid,
            "outcome": report.outcome,
            "duration": report.duration,
            "error_message": None,
        }

        if report.failed:
            if hasattr(report, "longreprtext"):
                result["error_message"] = report.longreprtext
            elif hasattr(report.longrepr, "reprcrash"):
                result["error_message"] = str(report.longrepr.reprcrash)

        self.results.append(result)

    def pytest_collection_modifyitems(self, items):
        """Log collected test items."""
        logger.info(f"Collected {len(items)} tests")


class TestExecutor:
    """
    Executes test jobs using labgrid and pytest.

    The executor:
    1. Downloads firmware artifacts
    2. Runs pytest with labgrid plugin for device control
    3. Collects results via custom plugin
    4. Uploads logs to storage
    """

    def __init__(self, lab_name: str, targets_dir: Path, tests_dir: Path):
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
            timeout=httpx.Timeout(300.0),
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
        job_id = job.get("id") or job.get("_id")
        job_data = job.get("data", {})
        device_type = job_data.get("device_type")
        tests = job_data.get("tests", [])
        timeout = job_data.get("timeout", 1800)

        # Get firmware info from parent node if available
        firmware_id = job.get("parent", "")
        firmware_url = job_data.get("firmware_url")

        logger.info(f"Executing job {job_id} on device {device_type}")

        start_time = datetime.utcnow()
        test_results: list[TestResult] = []
        console_log_url = None

        try:
            with tempfile.TemporaryDirectory(prefix=f"job-{job_id}-") as tmpdir:
                tmpdir_path = Path(tmpdir)
                console_log_path = tmpdir_path / "console.log"

                # Download firmware if URL provided
                firmware_path = None
                if firmware_url:
                    firmware_path = await self._download_firmware(
                        url=firmware_url,
                        dest_dir=tmpdir_path,
                    )

                # Run pytest and collect results
                collector, output = self._run_pytest(
                    device_type=device_type,
                    tests=tests,
                    firmware_path=firmware_path,
                    timeout=timeout,
                )

                # Save console output
                console_log_path.write_text(output)

                # Convert collected results
                test_results = self._convert_results(
                    collector=collector,
                    job_id=job_id,
                    firmware_id=firmware_id,
                    device_type=device_type,
                )

                # Upload console log
                if console_log_path.exists():
                    console_log_url = await self._upload_log(
                        log_path=console_log_path,
                        job_id=job_id,
                    )

        except Exception as e:
            logger.exception(f"Job {job_id} failed: {e}")
            test_results = [
                TestResult(
                    id=f"{job_id}:error",
                    job_id=job_id,
                    firmware_id=firmware_id,
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

        passed = sum(1 for r in test_results if r.status == TestStatus.PASS)
        failed = sum(1 for r in test_results if r.status == TestStatus.FAIL)
        skipped = sum(1 for r in test_results if r.status == TestStatus.SKIP)
        errors = sum(1 for r in test_results if r.status == TestStatus.ERROR)

        return JobResult(
            job_id=job_id,
            firmware_id=firmware_id,
            device_type=device_type,
            lab_name=self.lab_name,
            status="pass" if (errors == 0 and failed == 0) else "fail",
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

    async def _download_firmware(self, url: str, dest_dir: Path) -> Path | None:
        """Download firmware from URL to cache directory."""
        filename = url.split("/")[-1]
        cache_path = self.cache_dir / filename
        if cache_path.exists():
            logger.info(f"Using cached firmware: {cache_path}")
            return cache_path

        logger.info(f"Downloading firmware: {url}")
        try:
            response = await self.http_client.get(url)
            response.raise_for_status()
            cache_path.write_bytes(response.content)
            return cache_path
        except Exception as e:
            logger.warning(f"Failed to download firmware: {e}")
            return None

    def _run_pytest(
        self,
        device_type: str,
        tests: list[str],
        firmware_path: Path | None,
        timeout: int,
    ) -> tuple[ResultCollectorPlugin, str]:
        """
        Run pytest programmatically and collect results.

        Returns:
            Tuple of (result collector plugin, console output)
        """
        target_file = self.targets_dir / f"{device_type}.yaml"

        # Build pytest arguments
        args = [
            str(self.tests_dir),
            "-v",
            "--tb=short",
            f"--lg-env={target_file}",
        ]

        # Filter specific tests if provided
        if tests:
            args.extend(["-k", " or ".join(tests)])

        # Set firmware path via environment
        env_backup = os.environ.copy()
        os.environ["LG_COORDINATOR"] = settings.lg_coordinator
        if firmware_path:
            os.environ["LG_FIRMWARE"] = str(firmware_path)

        # Create result collector plugin
        collector = ResultCollectorPlugin()

        # Capture stdout/stderr
        output_buffer = io.StringIO()

        try:
            with redirect_stdout(output_buffer), redirect_stderr(output_buffer):
                # Run pytest with our plugin
                # Note: pytest.main() returns exit code, not raises
                exit_code = pytest.main(args, plugins=[collector])

            logger.info(f"pytest completed with exit code: {exit_code}")

        finally:
            # Restore environment
            os.environ.clear()
            os.environ.update(env_backup)

        return collector, output_buffer.getvalue()

    def _convert_results(
        self,
        collector: ResultCollectorPlugin,
        job_id: str,
        firmware_id: str,
        device_type: str,
    ) -> list[TestResult]:
        """Convert collected pytest results to TestResult objects."""
        test_results = []

        status_map = {
            "passed": TestStatus.PASS,
            "failed": TestStatus.FAIL,
            "skipped": TestStatus.SKIP,
        }

        for result in collector.results:
            nodeid = result["nodeid"]
            test_name = nodeid.split("::")[-1] if "::" in nodeid else nodeid
            status = status_map.get(result["outcome"], TestStatus.ERROR)

            test_results.append(
                TestResult(
                    id=f"{job_id}:{test_name}",
                    job_id=job_id,
                    firmware_id=firmware_id,
                    device_type=device_type,
                    lab_name=self.lab_name,
                    test_name=test_name,
                    test_path=nodeid,
                    status=status,
                    duration=result["duration"],
                    start_time=collector.start_time or datetime.utcnow(),
                    error_message=result.get("error_message"),
                )
            )

        return test_results

    async def _upload_log(self, log_path: Path, job_id: str) -> str | None:
        """Upload console log to storage."""
        if not self._minio:
            return None

        try:
            object_name = f"logs/{job_id}/console.log"
            self._minio.fput_object(
                bucket_name="openwrt-logs",
                object_name=object_name,
                file_path=str(log_path),
                content_type="text/plain",
            )
            return f"http://{settings.minio_endpoint}/openwrt-logs/{object_name}"
        except Exception as e:
            logger.warning(f"Failed to upload log: {e}")
            return None
