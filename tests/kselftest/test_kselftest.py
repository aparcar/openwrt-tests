"""
Linux kernel selftest (kselftest) test wrappers.

These tests run kselftest subsystems on the target device and output
their results in KTAP format. The test executor parses the KTAP output
to extract individual subtest results for reporting to KernelCI.

Each test function corresponds to a kselftest subsystem/category.
The raw KTAP output is captured via stdout and parsed by the executor.

Test Plan Mapping:
    kselftest_net      -> test_kselftest_net
    kselftest_timers   -> test_kselftest_timers
    kselftest_rtc      -> test_kselftest_rtc
    etc.

Note: These tests always "pass" at the pytest level - the actual
pass/fail status is determined by parsing the KTAP output. This is
because a single kselftest run may have dozens of subtests, some
passing and some failing.
"""

import pytest


class TestKselftestNet:
    """Network subsystem kselftests."""

    def test_kselftest_net(self, kselftest_runner):
        """
        Run network kselftests.

        Includes tests for:
        - Socket operations
        - Network namespaces
        - TCP/UDP functionality
        - BPF networking
        - etc.

        Requires: isolated_network capability
        """
        output = kselftest_runner("net", timeout=1800)
        # Output is captured for KTAP parsing
        # We don't assert here - subtests are parsed from KTAP


class TestKselftestTimers:
    """Timer subsystem kselftests."""

    def test_kselftest_timers(self, kselftest_runner):
        """
        Run timer kselftests.

        Includes tests for:
        - POSIX timers
        - Clock operations
        - Timer precision
        """
        output = kselftest_runner("timers", timeout=600)


class TestKselftestRtc:
    """RTC (Real-Time Clock) kselftests."""

    def test_kselftest_rtc(self, kselftest_runner):
        """
        Run RTC kselftests.

        Tests real-time clock functionality.
        """
        output = kselftest_runner("rtc", timeout=300)


class TestKselftestClone3:
    """clone3() syscall kselftests."""

    def test_kselftest_clone3(self, kselftest_runner):
        """
        Run clone3 kselftests.

        Tests the clone3() system call functionality.
        """
        output = kselftest_runner("clone3", timeout=300)


class TestKselftestOpenat2:
    """openat2() syscall kselftests."""

    def test_kselftest_openat2(self, kselftest_runner):
        """
        Run openat2 kselftests.

        Tests the openat2() system call functionality.
        """
        output = kselftest_runner("openat2", timeout=300)


class TestKselftestExec:
    """Exec subsystem kselftests."""

    def test_kselftest_exec(self, kselftest_runner):
        """
        Run exec kselftests.

        Tests execve() and related functionality.
        """
        output = kselftest_runner("exec", timeout=300)


class TestKselftestMincore:
    """mincore() syscall kselftests."""

    def test_kselftest_mincore(self, kselftest_runner):
        """
        Run mincore kselftests.

        Tests the mincore() system call.
        """
        output = kselftest_runner("mincore", timeout=300)


class TestKselftestSplice:
    """splice() syscall kselftests."""

    def test_kselftest_splice(self, kselftest_runner):
        """
        Run splice kselftests.

        Tests splice(), tee(), and vmsplice() system calls.
        """
        output = kselftest_runner("splice", timeout=300)


class TestKselftestSync:
    """Sync kselftests."""

    def test_kselftest_sync(self, kselftest_runner):
        """
        Run sync kselftests.

        Tests sync(), fsync(), and related functionality.
        """
        output = kselftest_runner("sync", timeout=300)


class TestKselftestFutex:
    """Futex kselftests."""

    def test_kselftest_futex(self, kselftest_runner):
        """
        Run futex kselftests.

        Tests futex operations for thread synchronization.
        """
        output = kselftest_runner("futex", timeout=600)


class TestKselftestMqueue:
    """POSIX message queue kselftests."""

    def test_kselftest_mqueue(self, kselftest_runner):
        """
        Run mqueue kselftests.

        Tests POSIX message queue functionality.
        """
        output = kselftest_runner("mqueue", timeout=300)


class TestKselftestSigaltstack:
    """sigaltstack() kselftests."""

    def test_kselftest_sigaltstack(self, kselftest_runner):
        """
        Run sigaltstack kselftests.

        Tests alternate signal stack functionality.
        """
        output = kselftest_runner("sigaltstack", timeout=300)


class TestKselftestKcmp:
    """kcmp() syscall kselftests."""

    def test_kselftest_kcmp(self, kselftest_runner):
        """
        Run kcmp kselftests.

        Tests the kcmp() system call for comparing processes.
        """
        output = kselftest_runner("kcmp", timeout=300)


class TestKselftestSize:
    """Size/memory kselftests."""

    def test_kselftest_size(self, kselftest_runner):
        """
        Run size kselftests.

        Tests related to memory sizes and limits.
        """
        output = kselftest_runner("size", timeout=300)
