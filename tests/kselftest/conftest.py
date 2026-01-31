"""
Pytest fixtures for running Linux kernel selftests (kselftests).

These fixtures provide the ability to run kselftest binaries on
the target device and capture their KTAP output for parsing.

The KTAP output is captured via pytest's stdout capture mechanism,
allowing the executor to parse individual subtest results.

KTAP Output Format:
    Kselftests output results in KTAP (Kernel Test Anything Protocol)
    format, which looks like:

        KTAP version 1
        1..3
        ok 1 - test_name_a
        not ok 2 - test_name_b # SKIP reason
        ok 3 - test_name_c

    The executor parses this output to report individual subtest
    results to KernelCI.

See: https://docs.kernel.org/dev-tools/ktap.html
"""

import logging

import pytest

logger = logging.getLogger(__name__)

# Path where kselftests are installed on OpenWrt
KSELFTEST_PATH = "/usr/libexec/kselftest"


class KselftestError(Exception):
    """Error running kselftest."""

    pass


class KselftestTimeout(KselftestError):
    """Kselftest execution timed out."""

    pass


def _validate_ktap_output(output: str, subsystem: str) -> None:
    """
    Validate that output looks like KTAP format.

    Logs a warning if output doesn't contain expected KTAP markers.
    This helps diagnose issues where tests run but don't produce
    parseable output.
    """
    if not output or not output.strip():
        logger.warning(
            f"Kselftest '{subsystem}' produced no output. "
            "The test may have crashed or not be installed correctly."
        )
        return

    ktap_markers = ["KTAP version", "TAP version", "1.."]
    if not any(marker in output for marker in ktap_markers):
        logger.warning(
            f"Kselftest '{subsystem}' output doesn't look like KTAP format. "
            "Subtest results may not be parsed correctly. "
            f"Output starts with: {output[:100]!r}"
        )


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

    Raises:
        pytest.skip: If the subsystem is not installed
        KselftestTimeout: If execution times out
        KselftestError: If execution fails unexpectedly
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
        try:
            result = shell_command.run(f"test -d {test_path}")
            if result[2] != 0:
                pytest.skip(f"Kselftest subsystem '{subsystem}' not installed")
        except Exception as e:
            logger.error(f"Failed to check if '{subsystem}' exists: {e}")
            pytest.skip(f"Cannot access kselftest path: {e}")

        # Determine how to run the tests
        run_script = f"{test_path}/run_kselftest.sh"
        try:
            result = shell_command.run(f"test -f {run_script}")
            has_run_script = result[2] == 0
        except Exception:
            has_run_script = False

        if has_run_script:
            cmd = f"cd {test_path} && ./run_kselftest.sh"
        else:
            # Run all executables in the directory
            cmd = f"cd {test_path} && for t in *; do [ -x \"$t\" ] && ./$t; done"

        logger.info(f"Running kselftest: {cmd}")

        # Execute the tests
        try:
            output_lines, stderr_lines, exit_code = shell_command.run(
                cmd, timeout=timeout
            )
            output = "\n".join(output_lines)
        except TimeoutError as e:
            logger.error(f"Kselftest '{subsystem}' timed out after {timeout}s")
            raise KselftestTimeout(
                f"Kselftest '{subsystem}' timed out after {timeout}s"
            ) from e
        except Exception as e:
            logger.error(f"Kselftest '{subsystem}' failed: {e}")
            raise KselftestError(f"Kselftest '{subsystem}' failed: {e}") from e

        # Log exit code for debugging (kselftests may return non-zero for failures)
        if exit_code != 0:
            logger.info(
                f"Kselftest '{subsystem}' exited with code {exit_code} "
                "(non-zero is normal if some subtests failed)"
            )

        # Validate output format
        _validate_ktap_output(output, subsystem)

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

    Raises:
        pytest.skip: If the test binary is not found
        KselftestTimeout: If execution times out
        KselftestError: If execution fails unexpectedly
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

        # Check if test exists and is executable
        try:
            result = shell_command.run(f"test -x {test_path}")
            if result[2] != 0:
                pytest.skip(f"Kselftest '{subsystem}/{test_name}' not found")
        except Exception as e:
            logger.error(f"Failed to check if '{test_name}' exists: {e}")
            pytest.skip(f"Cannot access kselftest: {e}")

        logger.info(f"Running kselftest: {test_path}")

        # Execute the test
        try:
            output_lines, stderr_lines, exit_code = shell_command.run(
                test_path, timeout=timeout
            )
            output = "\n".join(output_lines)
        except TimeoutError as e:
            logger.error(
                f"Kselftest '{subsystem}/{test_name}' timed out after {timeout}s"
            )
            raise KselftestTimeout(
                f"Kselftest '{subsystem}/{test_name}' timed out"
            ) from e
        except Exception as e:
            logger.error(f"Kselftest '{subsystem}/{test_name}' failed: {e}")
            raise KselftestError(
                f"Kselftest '{subsystem}/{test_name}' failed: {e}"
            ) from e

        # Log exit code
        if exit_code != 0:
            logger.info(
                f"Kselftest '{test_name}' exited with code {exit_code}"
            )

        # Validate output
        _validate_ktap_output(output, f"{subsystem}/{test_name}")

        # Print output to stdout for KTAP capture
        print(output)

        return output

    return _run
