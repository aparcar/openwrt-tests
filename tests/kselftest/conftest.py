"""
Pytest fixtures for running Linux kernel selftests (kselftests).

These fixtures provide the ability to run kselftest binaries on
the target device and capture their KTAP output for parsing.

The KTAP output is captured via pytest's stdout capture mechanism,
allowing the executor to parse individual subtest results.
"""

import logging

import pytest

logger = logging.getLogger(__name__)

# Path where kselftests are installed on OpenWrt
KSELFTEST_PATH = "/usr/libexec/kselftest"


@pytest.fixture
def kselftest_runner(shell_command):
    """
    Fixture to run a kselftest subsystem and return raw output.

    The raw KTAP output is printed to stdout so it can be captured
    by the test executor for parsing into individual subtest results.

    Usage:
        def test_kselftest_net(kselftest_runner):
            output = kselftest_runner("net")
            # Output is also printed to stdout for KTAP parsing
    """

    def _run(subsystem: str, timeout: int = 300) -> str:
        """
        Run a kselftest subsystem.

        Args:
            subsystem: The kselftest subsystem to run (e.g., "net", "timers")
            timeout: Timeout in seconds for test execution

        Returns:
            Raw output from the kselftest run (KTAP format)
        """
        test_path = f"{KSELFTEST_PATH}/{subsystem}"

        # Check if the subsystem exists
        result = shell_command.run(f"test -d {test_path}")
        if result[2] != 0:
            pytest.skip(f"Kselftest subsystem '{subsystem}' not installed")

        # Run the kselftest
        # kselftests typically have a run_kselftest.sh script or we run individual tests
        run_script = f"{test_path}/run_kselftest.sh"
        result = shell_command.run(f"test -f {run_script}")

        if result[2] == 0:
            # Use the run script if available
            cmd = f"cd {test_path} && ./run_kselftest.sh"
        else:
            # Otherwise run all executables in the directory
            cmd = f"cd {test_path} && for t in *; do [ -x \"$t\" ] && ./$t; done"

        logger.info(f"Running kselftest: {cmd}")
        output_lines, _, exit_code = shell_command.run(cmd, timeout=timeout)
        output = "\n".join(output_lines)

        # Print output to stdout for KTAP capture by executor
        print(output)

        return output

    return _run


@pytest.fixture
def kselftest_single(shell_command):
    """
    Fixture to run a single kselftest binary.

    Useful for running individual tests within a subsystem.

    Usage:
        def test_specific_test(kselftest_single):
            output = kselftest_single("net", "reuseport_bpf")
    """

    def _run(subsystem: str, test_name: str, timeout: int = 300) -> str:
        """
        Run a single kselftest binary.

        Args:
            subsystem: The kselftest subsystem (e.g., "net")
            test_name: Name of the test binary to run
            timeout: Timeout in seconds

        Returns:
            Raw output from the test (KTAP format)
        """
        test_path = f"{KSELFTEST_PATH}/{subsystem}/{test_name}"

        result = shell_command.run(f"test -x {test_path}")
        if result[2] != 0:
            pytest.skip(f"Kselftest '{subsystem}/{test_name}' not found")

        logger.info(f"Running kselftest: {test_path}")
        output_lines, _, exit_code = shell_command.run(
            test_path, timeout=timeout
        )
        output = "\n".join(output_lines)

        # Print output to stdout for KTAP capture
        print(output)

        return output

    return _run
