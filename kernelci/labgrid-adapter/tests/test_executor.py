"""Tests for test executor."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from labgrid_kci_adapter.executor import ResultCollectorPlugin, TestExecutor
from labgrid_kci_adapter.models import TestStatus


class TestResultCollectorPlugin:
    """Tests for ResultCollectorPlugin."""

    def test_init(self):
        """Test plugin initialization."""
        plugin = ResultCollectorPlugin()
        assert plugin.results == []
        assert plugin.start_time is None
        assert plugin.end_time is None

    def test_pytest_sessionstart(self):
        """Test session start hook."""
        plugin = ResultCollectorPlugin()
        plugin.pytest_sessionstart(MagicMock())
        assert plugin.start_time is not None
        assert isinstance(plugin.start_time, datetime)

    def test_pytest_sessionfinish(self):
        """Test session finish hook."""
        plugin = ResultCollectorPlugin()
        plugin.pytest_sessionfinish(MagicMock(), 0)
        assert plugin.end_time is not None

    def test_pytest_runtest_logreport_call_phase(self):
        """Test collecting results from call phase."""
        plugin = ResultCollectorPlugin()

        # Create mock report for 'call' phase
        report = MagicMock()
        report.when = "call"
        report.nodeid = "test_example.py::test_pass"
        report.outcome = "passed"
        report.duration = 1.5
        report.failed = False

        plugin.pytest_runtest_logreport(report)

        assert len(plugin.results) == 1
        assert plugin.results[0]["nodeid"] == "test_example.py::test_pass"
        assert plugin.results[0]["outcome"] == "passed"
        assert plugin.results[0]["duration"] == 1.5
        assert plugin.results[0]["error_message"] is None

    def test_pytest_runtest_logreport_setup_phase_ignored(self):
        """Test that setup phase is ignored."""
        plugin = ResultCollectorPlugin()

        report = MagicMock()
        report.when = "setup"

        plugin.pytest_runtest_logreport(report)

        assert len(plugin.results) == 0

    def test_pytest_runtest_logreport_teardown_phase_ignored(self):
        """Test that teardown phase is ignored."""
        plugin = ResultCollectorPlugin()

        report = MagicMock()
        report.when = "teardown"

        plugin.pytest_runtest_logreport(report)

        assert len(plugin.results) == 0

    def test_pytest_runtest_logreport_failed_with_error(self):
        """Test collecting failed test with error message."""
        plugin = ResultCollectorPlugin()

        report = MagicMock()
        report.when = "call"
        report.nodeid = "test_example.py::test_fail"
        report.outcome = "failed"
        report.duration = 0.5
        report.failed = True
        report.longreprtext = "AssertionError: expected True"

        plugin.pytest_runtest_logreport(report)

        assert len(plugin.results) == 1
        assert plugin.results[0]["outcome"] == "failed"
        assert plugin.results[0]["error_message"] == "AssertionError: expected True"

    def test_pytest_collection_modifyitems(self):
        """Test collection hook logs items."""
        plugin = ResultCollectorPlugin()
        items = [MagicMock(), MagicMock(), MagicMock()]

        # Should not raise
        plugin.pytest_collection_modifyitems(items)


class TestTestExecutor:
    """Tests for TestExecutor."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            targets_dir = tmpdir_path / "targets"
            tests_dir = tmpdir_path / "tests"
            targets_dir.mkdir()
            tests_dir.mkdir()
            yield targets_dir, tests_dir

    @pytest.fixture
    def executor(self, temp_dirs):
        """Create a TestExecutor instance."""
        targets_dir, tests_dir = temp_dirs
        return TestExecutor(
            lab_name="test-lab",
            targets_dir=targets_dir,
            tests_dir=tests_dir,
        )

    def test_init(self, executor, temp_dirs):
        """Test executor initialization."""
        targets_dir, tests_dir = temp_dirs
        assert executor.lab_name == "test-lab"
        assert executor.targets_dir == targets_dir
        assert executor.tests_dir == tests_dir

    @pytest.mark.asyncio
    async def test_initialize(self, executor):
        """Test executor initialization creates HTTP client."""
        with patch("labgrid_kci_adapter.executor.settings") as mock_settings:
            mock_settings.minio_endpoint = ""
            mock_settings.firmware_cache = Path("/tmp/cache")

            await executor.initialize()

            assert executor._http_client is not None
            await executor.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup(self, executor):
        """Test executor cleanup closes HTTP client."""
        with patch("labgrid_kci_adapter.executor.settings") as mock_settings:
            mock_settings.minio_endpoint = ""
            mock_settings.firmware_cache = Path("/tmp/cache")

            await executor.initialize()
            await executor.cleanup()

            assert executor._http_client is None

    def test_convert_results(self, executor):
        """Test converting pytest results to TestResult objects."""
        collector = ResultCollectorPlugin()
        collector.start_time = datetime(2024, 1, 1, 12, 0, 0)
        collector.results = [
            {
                "nodeid": "test_example.py::test_pass",
                "outcome": "passed",
                "duration": 1.0,
                "error_message": None,
            },
            {
                "nodeid": "test_example.py::test_fail",
                "outcome": "failed",
                "duration": 0.5,
                "error_message": "AssertionError",
            },
            {
                "nodeid": "test_example.py::test_skip",
                "outcome": "skipped",
                "duration": 0.0,
                "error_message": None,
            },
        ]

        results = executor._convert_results(
            collector=collector,
            job_id="job-123",
            firmware_id="fw-456",
            device_type="test-device",
        )

        assert len(results) == 3

        # Check passed test
        assert results[0].test_name == "test_pass"
        assert results[0].status == TestStatus.PASS
        assert results[0].duration == 1.0

        # Check failed test
        assert results[1].test_name == "test_fail"
        assert results[1].status == TestStatus.FAIL
        assert results[1].error_message == "AssertionError"

        # Check skipped test
        assert results[2].test_name == "test_skip"
        assert results[2].status == TestStatus.SKIP

    @pytest.mark.asyncio
    async def test_download_firmware_cached(self, executor, temp_dirs):
        """Test firmware download uses cache."""
        with patch("labgrid_kci_adapter.executor.settings") as mock_settings:
            mock_settings.firmware_cache = temp_dirs[0]
            mock_settings.minio_endpoint = ""

            # Create cached file
            cache_file = temp_dirs[0] / "firmware.bin"
            cache_file.write_bytes(b"cached firmware")

            await executor.initialize()

            result = await executor._download_firmware(
                url="http://example.com/firmware.bin",
                dest_dir=temp_dirs[1],
            )

            assert result == cache_file
            await executor.cleanup()

    @pytest.mark.asyncio
    async def test_execute_job_success(self, executor, temp_dirs):
        """Test successful job execution."""
        targets_dir, tests_dir = temp_dirs

        # Create target file
        (targets_dir / "test-device.yaml").write_text("targets: {}")

        # Create test file
        (tests_dir / "test_example.py").write_text(
            "def test_pass(): pass\ndef test_another(): pass\n"
        )

        job = {
            "id": "job-123",
            "parent": "fw-456",
            "data": {
                "device_type": "test-device",
                "tests": [],
                "timeout": 60,
            },
        }

        with patch("labgrid_kci_adapter.executor.settings") as mock_settings:
            mock_settings.firmware_cache = temp_dirs[0] / "cache"
            mock_settings.minio_endpoint = ""
            mock_settings.lg_coordinator = "localhost:20408"
            mock_settings.tests_repo_url = ""
            mock_settings.tests_repo_branch = "main"
            mock_settings.tests_dir = tests_dir

            with patch(
                "labgrid_kci_adapter.executor.ensure_tests",
                new_callable=AsyncMock,
                return_value=tests_dir,
            ):
                with patch.object(
                    executor,
                    "_run_pytest",
                    return_value=(ResultCollectorPlugin(), "output"),
                ):
                    await executor.initialize()
                    result = await executor.execute_job(job)
                    await executor.cleanup()

        assert result.job_id == "job-123"
        assert result.lab_name == "test-lab"
        assert result.device_type == "test-device"

    def test_try_parse_ktap_with_valid_ktap(self, executor):
        """Test _try_parse_ktap with valid KTAP output."""
        ktap_output = """KTAP version 1
1..3
ok 1 - test_pass
not ok 2 - test_fail
ok 3 - test_skip # SKIP not supported
"""
        results = executor._try_parse_ktap(ktap_output, prefix="kselftest")

        assert results is not None
        assert len(results) == 3
        assert results[0]["name"] == "kselftest.test_pass"
        assert results[0]["status"] == "pass"
        assert results[1]["name"] == "kselftest.test_fail"
        assert results[1]["status"] == "fail"
        assert results[2]["name"] == "kselftest.test_skip"
        assert results[2]["status"] == "skip"

    def test_try_parse_ktap_with_nested_subtests(self, executor):
        """Test _try_parse_ktap with nested KTAP subtests."""
        ktap_output = """KTAP version 1
1..1
  KTAP version 1
  1..2
  ok 1 - child_a
  not ok 2 - child_b
ok 1 - parent_test
"""
        results = executor._try_parse_ktap(ktap_output, prefix="net")

        assert results is not None
        assert len(results) == 2
        assert results[0]["name"] == "net.parent_test.child_a"
        assert results[0]["status"] == "pass"
        assert results[1]["name"] == "net.parent_test.child_b"
        assert results[1]["status"] == "fail"

    def test_try_parse_ktap_with_no_ktap(self, executor):
        """Test _try_parse_ktap returns None for non-KTAP output."""
        regular_output = "Running tests...\nAll tests passed!\n"
        results = executor._try_parse_ktap(regular_output, prefix="test")

        assert results is None

    def test_try_parse_ktap_with_empty_output(self, executor):
        """Test _try_parse_ktap returns None for empty output."""
        assert executor._try_parse_ktap("", prefix="test") is None
        assert executor._try_parse_ktap(None, prefix="test") is None

    def test_convert_results_with_ktap_output(self, executor):
        """Test _convert_results expands KTAP results into multiple TestResults."""
        collector = ResultCollectorPlugin()
        collector.start_time = datetime(2024, 1, 1, 12, 0, 0)
        collector.results = [
            {
                "nodeid": "test_kselftest.py::test_kselftest_net",
                "outcome": "passed",
                "duration": 10.0,
                "error_message": None,
                "stdout": """KTAP version 1
1..3
ok 1 - socket_test
not ok 2 - bind_test # FAIL address in use
ok 3 - listen_test # SKIP requires root
""",
                "stderr": None,
            },
        ]

        results = executor._convert_results(
            collector=collector,
            job_id="job-123",
            firmware_id="fw-456",
            device_type="test-device",
        )

        # Should expand to 3 KTAP subtests
        assert len(results) == 3

        # Check each subtest result
        assert results[0].test_name == "test_kselftest_net.socket_test"
        assert results[0].status == TestStatus.PASS

        assert results[1].test_name == "test_kselftest_net.bind_test"
        assert results[1].status == TestStatus.FAIL
        assert results[1].error_message == "FAIL address in use"

        assert results[2].test_name == "test_kselftest_net.listen_test"
        assert results[2].status == TestStatus.SKIP

    def test_convert_results_mixed_ktap_and_regular(self, executor):
        """Test _convert_results handles mix of KTAP and regular tests."""
        collector = ResultCollectorPlugin()
        collector.start_time = datetime(2024, 1, 1, 12, 0, 0)
        collector.results = [
            # Regular pytest test (no KTAP)
            {
                "nodeid": "test_boot.py::test_boot_success",
                "outcome": "passed",
                "duration": 5.0,
                "error_message": None,
                "stdout": "Device booted successfully\n",
                "stderr": None,
            },
            # KTAP kselftest
            {
                "nodeid": "test_kselftest.py::test_kselftest_timers",
                "outcome": "passed",
                "duration": 10.0,
                "error_message": None,
                "stdout": """TAP version 13
1..2
ok 1 - timer_create
ok 2 - timer_delete
""",
                "stderr": None,
            },
        ]

        results = executor._convert_results(
            collector=collector,
            job_id="job-123",
            firmware_id="fw-456",
            device_type="test-device",
        )

        # Should have 1 regular + 2 KTAP = 3 results
        assert len(results) == 3

        # First is regular pytest result
        assert results[0].test_name == "test_boot_success"
        assert results[0].status == TestStatus.PASS

        # Next two are KTAP subtests
        assert results[1].test_name == "test_kselftest_timers.timer_create"
        assert results[2].test_name == "test_kselftest_timers.timer_delete"

    def test_result_collector_captures_stdout(self):
        """Test ResultCollectorPlugin captures stdout from report sections."""
        plugin = ResultCollectorPlugin()

        report = MagicMock()
        report.when = "call"
        report.nodeid = "test_example.py::test_ktap"
        report.outcome = "passed"
        report.duration = 1.0
        report.failed = False
        report.sections = [
            ("Captured stdout call", "KTAP version 1\n1..1\nok 1 - test\n"),
        ]

        plugin.pytest_runtest_logreport(report)

        assert len(plugin.results) == 1
        assert plugin.results[0]["stdout"] == "KTAP version 1\n1..1\nok 1 - test\n"
