"""
Tests for KTAP parser module.
"""

import pytest

from labgrid_kci_adapter.ktap_parser import (
    KtapTestResult,
    TestStatus,
    ktap_results_to_dict,
    parse_ktap,
    summarize_results,
)


class TestParseKtapBasic:
    """Basic KTAP parsing tests."""

    def test_simple_pass(self):
        """Test parsing a simple passing test."""
        output = """
KTAP version 1
1..1
ok 1 - test_simple
"""
        results = parse_ktap(output)
        assert len(results) == 1
        assert results[0].name == "test_simple"
        assert results[0].status == TestStatus.PASS
        assert results[0].number == 1

    def test_simple_fail(self):
        """Test parsing a simple failing test."""
        output = """
KTAP version 1
1..1
not ok 1 - test_fail
"""
        results = parse_ktap(output)
        assert len(results) == 1
        assert results[0].name == "test_fail"
        assert results[0].status == TestStatus.FAIL

    def test_multiple_tests(self):
        """Test parsing multiple tests."""
        output = """
KTAP version 1
1..3
ok 1 - test_a
not ok 2 - test_b
ok 3 - test_c
"""
        results = parse_ktap(output)
        assert len(results) == 3
        assert results[0].status == TestStatus.PASS
        assert results[1].status == TestStatus.FAIL
        assert results[2].status == TestStatus.PASS

    def test_tap_version_13(self):
        """Test parsing TAP version 13 output."""
        output = """
TAP version 13
1..2
ok 1 - test_one
ok 2 - test_two
"""
        results = parse_ktap(output)
        assert len(results) == 2

    def test_with_prefix(self):
        """Test parsing with a name prefix."""
        output = """
KTAP version 1
1..1
ok 1 - test_socket
"""
        results = parse_ktap(output, prefix="kselftest.net")
        assert len(results) == 1
        assert results[0].name == "kselftest.net.test_socket"
        assert results[0].raw_name == "test_socket"


class TestParseKtapDirectives:
    """Tests for KTAP directive handling."""

    def test_skip_directive(self):
        """Test SKIP directive parsing."""
        output = """
KTAP version 1
1..1
ok 1 - test_feature # SKIP not supported on this platform
"""
        results = parse_ktap(output)
        assert len(results) == 1
        assert results[0].status == TestStatus.SKIP
        assert results[0].directive == "SKIP"
        assert results[0].directive_reason == "not supported on this platform"

    def test_skip_not_ok(self):
        """Test SKIP with not ok (should still be skip)."""
        output = """
KTAP version 1
1..1
not ok 1 - test_feature # SKIP missing dependency
"""
        results = parse_ktap(output)
        assert results[0].status == TestStatus.SKIP

    def test_todo_directive(self):
        """Test TODO directive parsing."""
        output = """
KTAP version 1
1..1
not ok 1 - test_wip # TODO work in progress
"""
        results = parse_ktap(output)
        assert results[0].status == TestStatus.SKIP
        assert results[0].directive == "TODO"

    def test_xfail_directive_failed(self):
        """Test XFAIL when test fails (expected, so pass)."""
        output = """
KTAP version 1
1..1
not ok 1 - test_known_bug # XFAIL known issue #123
"""
        results = parse_ktap(output)
        assert results[0].status == TestStatus.PASS
        assert results[0].directive == "XFAIL"

    def test_xfail_directive_passed(self):
        """Test XFAIL when test passes unexpectedly (fail)."""
        output = """
KTAP version 1
1..1
ok 1 - test_known_bug # XFAIL expected to fail
"""
        results = parse_ktap(output)
        assert results[0].status == TestStatus.FAIL

    def test_timeout_directive(self):
        """Test TIMEOUT directive parsing."""
        output = """
KTAP version 1
1..1
not ok 1 - test_slow # TIMEOUT exceeded 30s limit
"""
        results = parse_ktap(output)
        assert results[0].status == TestStatus.ERROR
        assert results[0].directive == "TIMEOUT"

    def test_error_directive(self):
        """Test ERROR directive parsing."""
        output = """
KTAP version 1
1..1
not ok 1 - test_crash # ERROR segmentation fault
"""
        results = parse_ktap(output)
        assert results[0].status == TestStatus.ERROR
        assert results[0].directive == "ERROR"


class TestParseKtapNested:
    """Tests for nested KTAP subtest parsing."""

    def test_simple_nested(self):
        """Test simple nested subtests."""
        output = """
KTAP version 1
1..1
  KTAP version 1
  1..2
  ok 1 - subtest_a
  ok 2 - subtest_b
ok 1 - parent_test
"""
        results = parse_ktap(output)
        assert len(results) == 2
        assert results[0].name == "parent_test.subtest_a"
        assert results[1].name == "parent_test.subtest_b"

    def test_nested_with_prefix(self):
        """Test nested subtests with a prefix."""
        output = """
KTAP version 1
1..1
  KTAP version 1
  1..1
  ok 1 - child
ok 1 - parent
"""
        results = parse_ktap(output, prefix="kselftest.net")
        assert len(results) == 1
        assert results[0].name == "kselftest.net.parent.child"

    def test_mixed_nested_and_flat(self):
        """Test mix of nested and non-nested tests."""
        output = """
KTAP version 1
1..3
ok 1 - simple_test
  KTAP version 1
  1..2
  ok 1 - nested_a
  not ok 2 - nested_b
not ok 2 - parent_with_subtests
ok 3 - another_simple
"""
        results = parse_ktap(output)
        assert len(results) == 4
        assert results[0].name == "simple_test"
        assert results[0].status == TestStatus.PASS
        assert results[1].name == "parent_with_subtests.nested_a"
        assert results[2].name == "parent_with_subtests.nested_b"
        assert results[2].status == TestStatus.FAIL
        assert results[3].name == "another_simple"

    def test_deeply_nested(self):
        """Test multiple levels of nesting."""
        output = """
KTAP version 1
1..1
  KTAP version 1
  1..1
    KTAP version 1
    1..2
    ok 1 - leaf_a
    ok 2 - leaf_b
  ok 1 - middle
ok 1 - top
"""
        results = parse_ktap(output)
        assert len(results) == 2
        assert results[0].name == "top.middle.leaf_a"
        assert results[1].name == "top.middle.leaf_b"


class TestParseKtapDiagnostic:
    """Tests for diagnostic line handling."""

    def test_diagnostic_before_result(self):
        """Test diagnostic lines captured before result."""
        output = """
KTAP version 1
1..1
# Running test for socket operations
# Testing IPv4
not ok 1 - test_socket
"""
        results = parse_ktap(output)
        assert len(results) == 1
        assert results[0].diagnostic is not None
        assert "Running test" in results[0].diagnostic
        assert "IPv4" in results[0].diagnostic

    def test_diagnostic_not_mixed_between_tests(self):
        """Test diagnostics are associated with correct test."""
        output = """
KTAP version 1
1..2
# Info for test 1
ok 1 - test_one
# Info for test 2
not ok 2 - test_two
"""
        results = parse_ktap(output)
        assert len(results) == 2
        assert results[0].diagnostic == "Info for test 1"
        assert results[1].diagnostic == "Info for test 2"


class TestParseKtapEdgeCases:
    """Edge case tests for KTAP parser."""

    def test_empty_output(self):
        """Test parsing empty output."""
        results = parse_ktap("")
        assert results == []

    def test_only_version_and_plan(self):
        """Test output with only version and plan, no tests."""
        output = """
KTAP version 1
1..0
"""
        results = parse_ktap(output)
        assert results == []

    def test_no_test_name(self):
        """Test result line without test name."""
        output = """
KTAP version 1
1..1
ok 1
"""
        results = parse_ktap(output)
        assert len(results) == 1
        assert results[0].name == "test_1"

    def test_test_name_with_spaces(self):
        """Test result with spaces in test name."""
        output = """
KTAP version 1
1..1
ok 1 - test with spaces in name
"""
        results = parse_ktap(output)
        assert results[0].name == "test with spaces in name"

    def test_case_insensitive_directives(self):
        """Test that directives are case-insensitive."""
        output = """
KTAP version 1
1..3
ok 1 - test_a # skip reason
ok 2 - test_b # Skip reason
ok 3 - test_c # SKIP reason
"""
        results = parse_ktap(output)
        assert all(r.status == TestStatus.SKIP for r in results)
        assert all(r.directive == "SKIP" for r in results)

    def test_unknown_directive_as_message(self):
        """Test unknown directive text becomes message."""
        output = """
KTAP version 1
1..1
not ok 1 - test_fail # assertion failed at line 42
"""
        results = parse_ktap(output)
        assert results[0].status == TestStatus.FAIL
        assert results[0].directive is None
        assert results[0].directive_reason == "assertion failed at line 42"


class TestParseKtapRealWorld:
    """Tests with real-world-like KTAP output."""

    def test_kselftest_net_sample(self):
        """Test parsing sample kselftest net output."""
        output = """
TAP version 13
1..4
# selftests: net: reuseport_bpf
ok 1 selftests: net: reuseport_bpf
# selftests: net: reuseport_dualstack
not ok 2 selftests: net: reuseport_dualstack # SKIP ipv6 disabled
  TAP version 13
  1..3
  ok 1 socket_af_inet
  ok 2 socket_af_inet6
  not ok 3 socket_af_packet # SKIP requires CAP_NET_RAW
ok 3 selftests: net: socket
# selftests: net: rtnetlink
not ok 4 selftests: net: rtnetlink # exit=1
"""
        results = parse_ktap(output, prefix="kselftest")

        # Should have 6 results:
        # - reuseport_bpf (leaf)
        # - reuseport_dualstack (leaf, skip)
        # - socket subtests: socket_af_inet, socket_af_inet6, socket_af_packet
        # - rtnetlink (leaf)
        assert len(results) == 6

        # Check first test
        assert results[0].name == "kselftest.selftests: net: reuseport_bpf"
        assert results[0].status == TestStatus.PASS

        # Check skipped test
        assert results[1].name == "kselftest.selftests: net: reuseport_dualstack"
        assert results[1].status == TestStatus.SKIP

        # Check nested tests (socket subtests)
        assert "socket_af_inet" in results[2].name
        assert results[2].status == TestStatus.PASS

        assert "socket_af_inet6" in results[3].name
        assert results[3].status == TestStatus.PASS

        assert "socket_af_packet" in results[4].name
        assert results[4].status == TestStatus.SKIP

        # Check final test
        assert results[5].name == "kselftest.selftests: net: rtnetlink"
        assert results[5].status == TestStatus.FAIL

    def test_kunit_sample(self):
        """Test parsing sample KUnit output."""
        output = """
KTAP version 1
1..1
  KTAP version 1
  # Subtest: example_test_suite
  1..3
  ok 1 - example_simple_test
  ok 2 - example_skip_test # SKIP skip reason
  not ok 3 - example_fail_test
    # example_fail_test: EXPECTATION FAILED at lib/test.c:42
    # Expected 1 == 2
ok 1 - example_test_suite
"""
        results = parse_ktap(output)

        assert len(results) == 3
        assert results[0].name == "example_test_suite.example_simple_test"
        assert results[0].status == TestStatus.PASS

        assert results[1].name == "example_test_suite.example_skip_test"
        assert results[1].status == TestStatus.SKIP

        assert results[2].name == "example_test_suite.example_fail_test"
        assert results[2].status == TestStatus.FAIL


class TestKtapResultsToDictAndSummarize:
    """Tests for helper functions."""

    def test_ktap_results_to_dict(self):
        """Test converting results to dict format."""
        results = [
            KtapTestResult(
                name="test_a",
                status=TestStatus.PASS,
                number=1,
            ),
            KtapTestResult(
                name="test_b",
                status=TestStatus.FAIL,
                directive_reason="assertion failed",
                number=2,
            ),
        ]

        dicts = ktap_results_to_dict(results)

        assert len(dicts) == 2
        assert dicts[0] == {
            "name": "test_a",
            "status": "pass",
            "duration": 0,
            "error_message": None,
        }
        assert dicts[1] == {
            "name": "test_b",
            "status": "fail",
            "duration": 0,
            "error_message": "assertion failed",
        }

    def test_summarize_results(self):
        """Test result summarization."""
        results = [
            KtapTestResult(name="t1", status=TestStatus.PASS, number=1),
            KtapTestResult(name="t2", status=TestStatus.PASS, number=2),
            KtapTestResult(name="t3", status=TestStatus.FAIL, number=3),
            KtapTestResult(name="t4", status=TestStatus.SKIP, number=4),
            KtapTestResult(name="t5", status=TestStatus.ERROR, number=5),
        ]

        summary = summarize_results(results)

        assert summary == {
            "total": 5,
            "passed": 2,
            "failed": 1,
            "skipped": 1,
            "errors": 1,
        }
