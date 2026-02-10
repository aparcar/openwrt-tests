"""
Test Executor for Labgrid

Executes test jobs using labgrid for device control and pytest
for test execution. Uses pytest's programmatic API for execution
and result collection.

For kselftest jobs, the executor parses KTAP output from test stdout
to extract individual subtest results.
"""

import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

import httpx
from minio import Minio

from .config import settings
from .ktap_parser import ktap_results_to_dict, parse_ktap
from .labgrid_client import LabgridClient
from .models import JobResult, TestResult, TestStatus
from .test_sync import ensure_tests

logger = logging.getLogger(__name__)

# Pattern to match ANSI/VT100 escape sequences (colors, cursor, screen control)
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-9;?]*[a-zA-Z]|[a-zA-Z])")


class ResultCollectorPlugin:
    """
    Pytest plugin to collect test results programmatically.

    Captures test outcomes, durations, and error messages without
    requiring external JSON report files.

    For kselftest tests, also captures stdout which may contain KTAP
    output for subtest parsing.
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
            "stdout": None,
            "stderr": None,
        }

        if report.failed:
            if hasattr(report, "longreprtext"):
                result["error_message"] = report.longreprtext
            elif hasattr(report.longrepr, "reprcrash"):
                result["error_message"] = str(report.longrepr.reprcrash)

        # Capture stdout/stderr for KTAP parsing
        if hasattr(report, "capstdout") and report.capstdout:
            result["stdout"] = report.capstdout
        if hasattr(report, "capstderr") and report.capstderr:
            result["stderr"] = report.capstderr

        # Also check sections for captured output
        for section_name, content in report.sections:
            if "stdout" in section_name.lower() and content:
                result["stdout"] = content
            elif "stderr" in section_name.lower() and content:
                result["stderr"] = content

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
        self._labgrid_client = LabgridClient()

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
        firmware_id = job.get("parent")  # None if no parent
        firmware_url = job_data.get("firmware_url")

        # Test type for logging/debugging
        test_type = job_data.get("test_type", "firmware")

        # Tests can be fetched per-job (LAVA pattern) or use static tests_dir
        tests_repo_url = job_data.get("tests_repo")
        tests_repo_branch = job_data.get("tests_branch", "main")
        tests_subdir = job_data.get("tests_subdir")  # Override for kselftest, etc.

        logger.info(
            f"Executing job {job_id} on device {device_type} "
            f"(test_type={test_type})"
        )

        start_time = datetime.utcnow()
        test_results: list[TestResult] = []
        console_log_url = None
        boot_log_url = None

        # Construct place name
        place_name = f"{self.lab_name}-{device_type}"

        try:
            # Acquire the labgrid place before running tests
            logger.info(f"Acquiring place: {place_name}")
            if not await self._labgrid_client.acquire_place(place_name):
                raise RuntimeError(f"Failed to acquire place: {place_name}")

            with tempfile.TemporaryDirectory(prefix=f"job-{job_id}-") as tmpdir:
                tmpdir_path = Path(tmpdir)
                console_log_path = tmpdir_path / "console.log"
                lg_log_dir = tmpdir_path / "labgrid-logs"
                lg_log_dir.mkdir(exist_ok=True)

                # Ensure tests are up-to-date before execution
                # Uses per-job repo if specified, otherwise uses configured repo
                # tests_subdir can be overridden per-job (e.g., for kselftest)
                tests_dir = await ensure_tests(
                    repo_url=tests_repo_url,
                    branch=tests_repo_branch,
                    subdir=tests_subdir,
                )

                # Download firmware if URL provided
                firmware_path = None
                if firmware_url:
                    firmware_path = await self._download_firmware(
                        url=firmware_url,
                        dest_dir=tmpdir_path,
                    )

                # Run pytest and collect results
                collector, output = await self._run_pytest(
                    device_type=device_type,
                    tests=tests,
                    tests_dir=tests_dir,
                    firmware_path=firmware_path,
                    timeout=timeout,
                    log_dir=lg_log_dir,
                )

                # Strip ANSI escape codes from pytest output
                output = _ANSI_ESCAPE_RE.sub("", output)

                # Save console output
                console_log_path.write_text(output)

                # Convert collected results
                test_results = self._convert_results(
                    collector=collector,
                    job_id=job_id,
                    firmware_id=firmware_id,
                    device_type=device_type,
                )

                # Split pytest output into per-test log sections and upload
                per_test_logs = self._split_pytest_output(output)
                for tr in test_results:
                    test_log = per_test_logs.get(tr.test_name)
                    if test_log:
                        log_path = tmpdir_path / f"test-{tr.test_name}.log"
                        log_path.write_text(test_log)
                        tr.log_url = await self._upload_log(
                            log_path=log_path,
                            job_id=job_id,
                            log_name=f"test-{tr.test_name}.log",
                        )

                # Find boot log (serial console output from labgrid)
                boot_log_path = self._find_boot_log(lg_log_dir)

                # Strip ANSI codes from boot log too
                if boot_log_path and boot_log_path.exists():
                    boot_content = boot_log_path.read_text(errors="replace")
                    boot_content = _ANSI_ESCAPE_RE.sub("", boot_content)
                    boot_log_path.write_text(boot_content)

                # Upload boot log separately for dashboard boot section
                boot_log_url = None
                if boot_log_path and boot_log_path.exists():
                    boot_log_url = await self._upload_log(
                        log_path=boot_log_path,
                        job_id=job_id,
                        log_name="boot.log",
                    )

                # Combine boot log + pytest output into single log file
                combined_log_path = tmpdir_path / "combined.log"
                await self._combine_logs(
                    boot_log_path=boot_log_path,
                    console_log_path=console_log_path,
                    output_path=combined_log_path,
                )

                # Upload combined log
                if combined_log_path.exists():
                    console_log_url = await self._upload_log(
                        log_path=combined_log_path,
                        job_id=job_id,
                        log_name="console.log",
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

        finally:
            # Always release the place after test execution
            logger.info(f"Releasing place: {place_name}")
            await self._labgrid_client.release_place(place_name)

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
            status="pass" if (len(test_results) > 0 and errors == 0 and failed == 0) else "fail",
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
            boot_log_url=boot_log_url,
        )

    async def _download_firmware(self, url: str, dest_dir: Path) -> Path | None:
        """Download firmware from URL to cache directory.

        Automatically decompresses .gz files since QEMU and labgrid
        expect raw disk/kernel images.

        Uses zlib instead of gzip.decompress() because OpenWrt firmware
        .img.gz files have trailing data (e.g. "# fake certificate...")
        appended after gzip compression. Python's gzip module tries to
        parse trailing data as a second gzip member and fails, while
        zlib.decompressobj only decompresses the first member.
        """
        import zlib

        filename = url.split("/")[-1]
        # If compressed, the final cached file uses the decompressed name
        decompressed_name = filename.removesuffix(".gz")
        cache_path = self.cache_dir / decompressed_name

        if cache_path.exists():
            logger.info(f"Using cached firmware: {cache_path}")
            return cache_path

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            logger.info(f"Downloading firmware: {url} (attempt {attempt}/{max_retries})")
            try:
                response = await self.http_client.get(url)
                response.raise_for_status()
                data = response.content

                if filename.endswith(".gz"):
                    if len(data) < 2 or data[:2] != b'\x1f\x8b':
                        raise ValueError(
                            f"Expected gzip data but got {data[:20]!r}"
                        )
                    logger.info(f"Decompressing {filename} ({len(data)} bytes)")
                    # wbits=31 tells zlib to auto-detect gzip format
                    dec = zlib.decompressobj(wbits=31)
                    data = dec.decompress(data)

                cache_path.write_bytes(data)
                logger.info(
                    f"Firmware ready: {cache_path.name} "
                    f"({len(data) / 1024 / 1024:.1f} MB)"
                )
                return cache_path
            except Exception as e:
                logger.warning(f"Download attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(5 * attempt)

        logger.error(f"Failed to download firmware after {max_retries} attempts: {url}")
        return None

    async def _run_pytest(
        self,
        device_type: str,
        tests: list[str],
        tests_dir: Path,
        firmware_path: Path | None,
        timeout: int,
        log_dir: Path | None = None,
    ) -> tuple[ResultCollectorPlugin, str]:
        """
        Run pytest as a subprocess and collect results.

        Uses subprocess to avoid event loop conflicts with labgrid's
        async coordinator session.

        Args:
            device_type: Device type for labgrid target selection
            tests: List of test name patterns to run
            tests_dir: Directory containing pytest test files
            firmware_path: Path to firmware file (optional)
            timeout: Test timeout in seconds
            log_dir: Directory to store labgrid serial logs (boot log)

        Returns:
            Tuple of (result collector plugin, console output)
        """
        target_file = self.targets_dir / f"{device_type}.yaml"

        # Build pytest arguments
        # Match Makefile approach: --lg-log --log-cli-level=CONSOLE --lg-colored-steps
        # This streams all labgrid console output (boot log) directly to pytest output
        args = [
            "pytest",
            str(tests_dir),
            "-v",
            "--tb=short",
            f"--lg-env={target_file}",
            # Stream all logging (including labgrid serial console) to output
            "--log-cli-level=CONSOLE",
            # Show labgrid step markers in output
            "--lg-colored-steps",
            # Ignore kselftest directory — those are scheduled as separate jobs
            f"--ignore={tests_dir / 'kselftest'}",
        ]

        # Add labgrid logging to capture serial console (boot log)
        if log_dir:
            args.append(f"--lg-log={log_dir}")

        # Pass firmware path to pytest (used by conftest.py setup_env fixture
        # to set labgrid images.firmware for QEMUDriver disk)
        if firmware_path:
            args.extend(["--firmware", str(firmware_path)])

        # Filter specific tests if provided
        if tests:
            args.extend(["-k", " or ".join(tests)])

        # Set labgrid environment variables
        env = os.environ.copy()
        env["LG_COORDINATOR"] = settings.lg_coordinator
        # LG_PLACE is the labgrid place name for remote device access
        env["LG_PLACE"] = f"{settings.lab_name}-{device_type}"
        if firmware_path:
            # LG_IMAGE is used by target YAML templates for firmware path
            env["LG_IMAGE"] = str(firmware_path)
            # Also set LG_FIRMWARE for backwards compatibility
            env["LG_FIRMWARE"] = str(firmware_path)

        # Run pytest as subprocess
        logger.info(f"Running pytest: {' '.join(args)}")
        proc = await asyncio.create_subprocess_exec(
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(self.targets_dir.parent),  # Run from labgrid-runner dir
        )

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error(f"pytest timed out after {timeout}s")
            stdout = b"pytest timed out"

        output = stdout.decode("utf-8", errors="replace")
        exit_code = proc.returncode

        logger.info(f"pytest completed with exit code: {exit_code}")

        # Log output if pytest failed or had issues
        if exit_code != 0:
            logger.warning(f"pytest output:\n{output[-2000:]}")  # Last 2000 chars

        # Parse results from pytest output
        # Create a collector to hold results (parsed from output)
        collector = ResultCollectorPlugin()
        collector.start_time = datetime.utcnow()
        collector.end_time = datetime.utcnow()

        # Try to parse pytest output for test results
        collector.results = self._parse_pytest_output(output)

        return collector, output

    def _parse_pytest_output(self, output: str) -> list[dict]:
        """
        Parse pytest verbose output to extract test results.

        pytest verbose output can split across lines:
          tests/test_base.py::test_shell
          PASSED                                                 [ 36%]
        or be on a single line:
          tests/test_apk.py::TestApk::test_apk SKIPPED (reason) [ 30%]

        Args:
            output: Raw pytest output

        Returns:
            List of result dicts with nodeid, outcome, duration
        """
        import re
        results = []

        outcome_map = {
            'passed': 'passed',
            'failed': 'failed',
            'skipped': 'skipped',
            'error': 'failed',
        }

        # Pattern for nodeid (file::class::method or file::function)
        nodeid_pattern = re.compile(
            r'^([\w/\-_\.]+::[\w:]+)'
        )
        # Pattern for status at start of line (multiline case)
        status_pattern = re.compile(
            r'^(PASSED|FAILED|SKIPPED|ERROR)\b'
        )
        # Pattern for same-line: nodeid STATUS
        sameline_pattern = re.compile(
            r'^([\w/\-_\.]+::[\w:]+)\s+(PASSED|FAILED|SKIPPED|ERROR)\b'
        )

        lines = output.split('\n')
        pending_nodeid = None

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Try same-line match first
            m = sameline_pattern.search(stripped)
            if m:
                pending_nodeid = None
                results.append({
                    'nodeid': m.group(1),
                    'outcome': outcome_map.get(m.group(2).lower(), 'failed'),
                    'duration': 0,
                    'error_message': None,
                    'stdout': None,
                    'stderr': None,
                })
                continue

            # Check if this line is a status (for a pending nodeid or standalone)
            sm = status_pattern.search(stripped)
            if sm:
                if pending_nodeid:
                    results.append({
                        'nodeid': pending_nodeid,
                        'outcome': outcome_map.get(sm.group(1).lower(), 'failed'),
                        'duration': 0,
                        'error_message': None,
                        'stdout': None,
                        'stderr': None,
                    })
                    pending_nodeid = None
                continue

            # Check if this line is a nodeid — a new test starting
            nm = nodeid_pattern.match(stripped)
            if nm:
                pending_nodeid = nm.group(1)
            # Don't reset pending_nodeid for other lines (labgrid log
            # output, live log markers, etc. appear between the nodeid
            # line and the PASSED/FAILED line)

        logger.info(f"Parsed {len(results)} test results from output")
        return results

    def _split_pytest_output(self, output: str) -> dict[str, str]:
        """
        Split pytest output into per-test log sections.

        Parses verbose pytest output to find where each test starts and ends,
        extracting the log section for each test. Test sections include the
        test name line, all labgrid/logging output during the test, and the
        PASSED/FAILED/SKIPPED result line.

        Args:
            output: Full pytest output (ANSI codes already stripped)

        Returns:
            Dict mapping test_name to its log section
        """
        # Match lines that start a test: "tests/test_base.py::TestClass::test_name"
        nodeid_re = re.compile(r"^([\w/\-_\.]+::([\w:]+))")
        # Match status lines
        status_re = re.compile(r"^(PASSED|FAILED|SKIPPED|ERROR)\b")
        # Match same-line: "tests/test_foo.py::test_bar PASSED"
        sameline_re = re.compile(
            r"^([\w/\-_\.]+::([\w:]+))\s+(PASSED|FAILED|SKIPPED|ERROR)\b"
        )

        lines = output.split("\n")
        sections: dict[str, list[str]] = {}
        current_test = None
        current_lines: list[str] = []

        for line in lines:
            stripped = line.strip()

            # Same-line match: nodeid + status on one line
            m = sameline_re.search(stripped)
            if m:
                if current_test:
                    # Save previous test section
                    sections[current_test] = current_lines
                # This test is a single line
                test_name = m.group(2).split("::")[-1]
                sections[test_name] = [line]
                current_test = None
                current_lines = []
                continue

            # Check for status line (ends current test)
            sm = status_re.search(stripped)
            if sm and current_test:
                current_lines.append(line)
                sections[current_test] = current_lines
                current_test = None
                current_lines = []
                continue

            # Check for new test starting
            nm = nodeid_re.match(stripped)
            if nm:
                if current_test:
                    # Save previous test section (no status line found)
                    sections[current_test] = current_lines
                test_name = nm.group(2).split("::")[-1]
                current_test = test_name
                current_lines = [line]
                continue

            # Accumulate lines for current test
            if current_test:
                current_lines.append(line)

        # Save last test if still pending
        if current_test:
            sections[current_test] = current_lines

        # Join lines into strings
        return {name: "\n".join(lines) for name, lines in sections.items()}

    def _convert_results(
        self,
        collector: ResultCollectorPlugin,
        job_id: str,
        firmware_id: str,
        device_type: str,
    ) -> list[TestResult]:
        """
        Convert collected pytest results to TestResult objects.

        For tests that contain KTAP output in their stdout, parse the
        KTAP to extract individual subtest results. This is used for
        kselftest tests that run multiple subtests and report via KTAP.
        """
        test_results = []

        # Pytest uses past tense: "passed", "failed", "skipped"
        pytest_status_map = {
            "passed": TestStatus.PASS,
            "failed": TestStatus.FAIL,
            "skipped": TestStatus.SKIP,
        }

        # KTAP uses present tense: "pass", "fail", "skip", "error"
        ktap_status_map = {
            "pass": TestStatus.PASS,
            "fail": TestStatus.FAIL,
            "skip": TestStatus.SKIP,
            "error": TestStatus.ERROR,
        }

        for result in collector.results:
            nodeid = result["nodeid"]
            test_name = nodeid.split("::")[-1] if "::" in nodeid else nodeid
            stdout = result.get("stdout", "")

            # Check if stdout contains KTAP output
            ktap_results = self._try_parse_ktap(stdout, test_name)

            if ktap_results:
                # Expand KTAP subtests into individual TestResult objects
                for ktap in ktap_results:
                    ktap_status = ktap_status_map.get(
                        ktap["status"], TestStatus.ERROR
                    )
                    test_results.append(
                        TestResult(
                            id=f"{job_id}:{ktap['name']}",
                            job_id=job_id,
                            firmware_id=firmware_id,
                            device_type=device_type,
                            lab_name=self.lab_name,
                            test_name=ktap["name"],
                            test_path=f"{nodeid}::{ktap['name']}",
                            status=ktap_status,
                            duration=ktap.get("duration", 0),
                            start_time=collector.start_time or datetime.utcnow(),
                            error_message=ktap.get("error_message"),
                        )
                    )
            else:
                # Standard pytest result (no KTAP)
                status = pytest_status_map.get(result["outcome"], TestStatus.ERROR)
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

    def _try_parse_ktap(
        self, output: str, prefix: str = ""
    ) -> list[dict] | None:
        """
        Try to parse KTAP output from test stdout.

        Returns parsed results if KTAP is detected, None otherwise.

        Args:
            output: Test stdout that may contain KTAP
            prefix: Prefix for test names (usually the parent test name)

        Returns:
            List of dicts with 'name', 'status', 'duration', 'error_message'
            or None if no KTAP detected
        """
        if not output:
            return None

        # Check for KTAP/TAP markers
        if not any(
            marker in output
            for marker in ["KTAP version", "TAP version", "1.."]
        ):
            return None

        try:
            ktap_results = parse_ktap(output, prefix=prefix)
            if ktap_results:
                logger.info(
                    f"Parsed {len(ktap_results)} subtests from KTAP output"
                )
                return ktap_results_to_dict(ktap_results)
        except Exception as e:
            logger.warning(f"Failed to parse KTAP output: {e}")

        return None

    async def _upload_log(
        self, log_path: Path, job_id: str, log_name: str = "console.log"
    ) -> str | None:
        """Upload a log file to storage."""
        if not self._minio:
            logger.debug("MinIO not configured, skipping log upload")
            return None

        try:
            bucket = settings.minio_logs_bucket
            object_name = f"logs/{job_id}/{log_name}"
            self._minio.fput_object(
                bucket_name=bucket,
                object_name=object_name,
                file_path=str(log_path),
                content_type="text/plain",
            )
            # Use https if minio_secure is enabled
            scheme = "https" if settings.minio_secure else "http"
            url = f"{scheme}://{settings.minio_endpoint}/{bucket}/{object_name}"
            logger.info(f"Uploaded log to {url}")
            return url
        except Exception as e:
            logger.warning(f"Failed to upload log {log_name}: {e}")
            return None

    def _find_boot_log(self, log_dir: Path) -> Path | None:
        """
        Find boot log (serial console output) from labgrid log directory.

        Labgrid's --lg-log option creates files like:
        - console_main (no .log extension)

        Returns the path to the boot log file, or None if not found.
        """
        if not log_dir.exists():
            return None

        try:
            # Find console log files created by labgrid
            # Labgrid creates files like "console_main" (no .log extension)
            console_logs = list(log_dir.glob("console_*"))
            if not console_logs:
                # Try alternative patterns
                console_logs = list(log_dir.glob("*serial*"))
            if not console_logs:
                # Fallback: any file in the directory
                console_logs = [f for f in log_dir.iterdir() if f.is_file()]

            if not console_logs:
                logger.debug(f"No boot log found in {log_dir}")
                return None

            # Use the largest/most recent log file
            boot_log = max(console_logs, key=lambda p: p.stat().st_size)
            logger.info(f"Found boot log: {boot_log.name} ({boot_log.stat().st_size} bytes)")
            return boot_log
        except Exception as e:
            logger.warning(f"Failed to find boot log: {e}")
            return None

    async def _combine_logs(
        self,
        boot_log_path: Path | None,
        console_log_path: Path,
        output_path: Path,
    ) -> None:
        """
        Combine boot log and pytest console output into a single file.

        The combined log shows:
        1. Boot log (serial console during device boot)
        2. Pytest output (test execution results)
        """
        try:
            with open(output_path, "w") as outfile:
                # Write boot log first (if available)
                if boot_log_path and boot_log_path.exists():
                    outfile.write("=" * 80 + "\n")
                    outfile.write("BOOT LOG (Serial Console)\n")
                    outfile.write("=" * 80 + "\n\n")
                    outfile.write(boot_log_path.read_text(errors="replace"))
                    outfile.write("\n\n")

                # Write pytest output
                if console_log_path.exists():
                    outfile.write("=" * 80 + "\n")
                    outfile.write("TEST OUTPUT (pytest)\n")
                    outfile.write("=" * 80 + "\n\n")
                    outfile.write(console_log_path.read_text(errors="replace"))

            logger.info(f"Combined logs written to {output_path}")
        except Exception as e:
            logger.warning(f"Failed to combine logs: {e}")
            # Fall back to just copying console log
            if console_log_path.exists():
                output_path.write_text(console_log_path.read_text(errors="replace"))
