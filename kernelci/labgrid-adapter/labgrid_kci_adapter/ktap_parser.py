"""
KTAP (Kernel Test Anything Protocol) parser.

Parses nested KTAP output from kselftests into flat test results
suitable for KernelCI node submission.

KTAP is an extension of TAP (Test Anything Protocol) used by the Linux
kernel for reporting test results from kselftests and KUnit tests.

Key features:
- Nested subtests via 2-space indentation
- Directives: SKIP, TODO, XFAIL, TIMEOUT, ERROR
- Diagnostic lines prefixed with #

Reference: https://docs.kernel.org/dev-tools/ktap.html

Example KTAP output:
    KTAP version 1
    1..2
      KTAP version 1
      1..3
      ok 1 - subtest_a
      not ok 2 - subtest_b # SKIP not supported
      ok 3 - subtest_c
    ok 1 - test_group
    not ok 2 - test_single # FAIL assertion failed

Note: In KTAP, subtests appear BEFORE the parent result line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class TestStatus(str, Enum):
    """
    Test result status.

    Note: This enum mirrors models.TestStatus. They are kept separate to
    avoid a pydantic dependency in the KTAP parser module. The string
    values ("pass", "fail", "skip", "error") must stay in sync.
    """

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


@dataclass
class KtapTestResult:
    """
    Individual test result from KTAP output.

    Attributes:
        name: Hierarchical test name (e.g., "net.socket.af_inet")
        status: Test result status
        directive: Optional directive (SKIP, TODO, XFAIL, etc.)
        directive_reason: Reason provided with directive
        diagnostic: Diagnostic/error message from # lines
        number: Test number from KTAP output
        raw_name: Original test name before hierarchical prefixing
    """

    name: str
    status: TestStatus
    directive: str | None = None
    directive_reason: str | None = None
    diagnostic: str | None = None
    number: int = 0
    raw_name: str = ""


@dataclass
class _ParsedLine:
    """Internal representation of a parsed KTAP line."""

    line_type: str  # "version", "plan", "result", "diagnostic", "unknown"
    indent_level: int = 0
    raw_line: str = ""
    # For result lines
    is_ok: bool = False
    test_number: int = 0
    test_name: str = ""
    directive: str | None = None
    directive_reason: str | None = None
    # For plan lines
    plan_count: int = 0
    # For diagnostic lines
    diagnostic_text: str = ""


# Regex patterns for KTAP parsing
_VERSION_PATTERN = re.compile(r"^(KTAP version|TAP version)\s+(\d+)")
_PLAN_PATTERN = re.compile(r"^1\.\.(\d+)")
_RESULT_PATTERN = re.compile(
    r"^(ok|not ok)\s+(\d+)\s*(?:-\s*)?([^#]*?)(?:\s*#\s*(.*))?$"
)
_DIRECTIVE_PATTERN = re.compile(
    r"^(SKIP|TODO|XFAIL|TIMEOUT|ERROR)(?:\s+(.*))?$", re.IGNORECASE
)


def parse_ktap(output: str, prefix: str = "") -> list[KtapTestResult]:
    """
    Parse KTAP output into flat list of test results.

    Handles nested KTAP output by flattening the hierarchy into
    dot-separated test names suitable for KernelCI submission.

    Args:
        output: Raw KTAP output string
        prefix: Optional prefix for all test names (e.g., "kselftest.net")

    Returns:
        List of KtapTestResult objects, one per test/subtest

    Example:
        >>> output = '''
        ... KTAP version 1
        ... 1..2
        ... ok 1 - test_a
        ... not ok 2 - test_b # SKIP no support
        ... '''
        >>> results = parse_ktap(output, prefix="kselftest.net")
        >>> results[0].name
        'kselftest.net.test_a'
        >>> results[1].status
        <TestStatus.SKIP: 'skip'>
    """
    lines = output.splitlines()
    parsed_lines = [_parse_line(line) for line in lines]
    return _process_parsed_lines(parsed_lines, prefix)


def _parse_line(line: str) -> _ParsedLine:
    """Parse a single line into a structured representation."""
    # Calculate indentation (2 spaces = 1 level)
    stripped = line.lstrip()
    indent_spaces = len(line) - len(stripped)
    indent_level = indent_spaces // 2

    result = _ParsedLine(
        line_type="unknown",
        indent_level=indent_level,
        raw_line=line,
    )

    if not stripped:
        return result

    # Check for version line
    if _VERSION_PATTERN.match(stripped):
        result.line_type = "version"
        return result

    # Check for plan line
    plan_match = _PLAN_PATTERN.match(stripped)
    if plan_match:
        result.line_type = "plan"
        result.plan_count = int(plan_match.group(1))
        return result

    # Check for diagnostic line
    if stripped.startswith("#"):
        result.line_type = "diagnostic"
        result.diagnostic_text = stripped[1:].strip()
        return result

    # Check for result line
    result_match = _RESULT_PATTERN.match(stripped)
    if result_match:
        ok_str, num_str, name, directive_str = result_match.groups()
        result.line_type = "result"
        result.is_ok = ok_str == "ok"
        result.test_number = int(num_str)
        result.test_name = name.strip() if name else f"test_{num_str}"

        # Parse directive if present
        if directive_str:
            directive_str = directive_str.strip()
            dir_match = _DIRECTIVE_PATTERN.match(directive_str)
            if dir_match:
                result.directive = dir_match.group(1).upper()
                result.directive_reason = dir_match.group(2)
            else:
                # Treat as reason/message if not a known directive
                result.directive_reason = directive_str

        return result

    return result


def _process_parsed_lines(
    lines: list[_ParsedLine],
    prefix: str,
) -> list[KtapTestResult]:
    """
    Process parsed lines into test results, handling nesting.

    In KTAP, subtests appear BEFORE the parent result line:
        KTAP version 1
        1..1
          KTAP version 1      <- subtest block starts
          1..2
          ok 1 - child_a
          ok 2 - child_b
        ok 1 - parent         <- parent result comes after

    This function collects subtest blocks and associates them with
    the next result line at the parent's indentation level.
    """
    results: list[KtapTestResult] = []
    idx = 0
    n = len(lines)

    def process_at_level(level: int, name_prefix: str) -> list[KtapTestResult]:
        """Process lines at a specific indentation level."""
        nonlocal idx
        level_results: list[KtapTestResult] = []
        pending_diagnostics: list[str] = []
        pending_subtests: list[KtapTestResult] = []

        while idx < n:
            line = lines[idx]

            # If we hit a line at a lower indent level, we're done with this level
            if line.line_type != "unknown" and line.indent_level < level:
                break

            # If we hit a higher indent level, it's a subtest block
            if line.indent_level > level:
                # Process the subtest block
                # We don't know the parent name yet, will be filled in when we
                # see the parent result line
                subtests = process_at_level(line.indent_level, "")
                pending_subtests.extend(subtests)
                continue

            # Process lines at our level
            idx += 1

            if line.line_type == "version":
                continue

            if line.line_type == "plan":
                continue

            if line.line_type == "diagnostic":
                pending_diagnostics.append(line.diagnostic_text)
                continue

            if line.line_type == "result":
                test_name = line.test_name

                # If we have pending subtests, they belong to this parent
                if pending_subtests:
                    # Update subtest names with parent prefix
                    parent_prefix = (
                        f"{name_prefix}.{test_name}" if name_prefix else test_name
                    )
                    for subtest in pending_subtests:
                        subtest.name = f"{parent_prefix}.{subtest.name}"
                    level_results.extend(pending_subtests)
                    pending_subtests = []
                    pending_diagnostics = []
                else:
                    # This is a leaf test (no subtests)
                    full_name = (
                        f"{name_prefix}.{test_name}" if name_prefix else test_name
                    )
                    status = _determine_status(line.is_ok, line.directive)

                    result = KtapTestResult(
                        name=full_name,
                        status=status,
                        directive=line.directive,
                        directive_reason=line.directive_reason,
                        diagnostic=(
                            "\n".join(pending_diagnostics)
                            if pending_diagnostics
                            else None
                        ),
                        number=line.test_number,
                        raw_name=test_name,
                    )
                    level_results.append(result)
                    pending_diagnostics = []

        return level_results

    results = process_at_level(0, prefix)
    return results


def _determine_status(is_ok: bool, directive: str | None) -> TestStatus:
    """
    Determine test status from ok/not ok and directive.

    Args:
        is_ok: True if line started with "ok", False for "not ok"
        directive: Optional directive (SKIP, TODO, XFAIL, etc.)

    Returns:
        Appropriate TestStatus value
    """
    if directive:
        directive_upper = directive.upper()
        if directive_upper == "SKIP":
            return TestStatus.SKIP
        elif directive_upper == "TODO":
            # TODO tests are expected to fail, treat as skip
            return TestStatus.SKIP
        elif directive_upper == "XFAIL":
            # Expected failure - if it failed as expected, it's a pass
            return TestStatus.PASS if not is_ok else TestStatus.FAIL
        elif directive_upper in ("TIMEOUT", "ERROR"):
            return TestStatus.ERROR

    return TestStatus.PASS if is_ok else TestStatus.FAIL


def ktap_results_to_dict(results: list[KtapTestResult]) -> list[dict]:
    """
    Convert KTAP results to dictionary format for KernelCI API.

    Args:
        results: List of KtapTestResult objects

    Returns:
        List of dicts with 'name', 'status', 'duration', 'error_message' keys
    """
    return [
        {
            "name": r.name,
            "status": r.status.value,
            "duration": 0,  # KTAP doesn't include timing info
            "error_message": r.directive_reason or r.diagnostic,
        }
        for r in results
    ]


def summarize_results(results: list[KtapTestResult]) -> dict:
    """
    Generate summary statistics for KTAP results.

    Args:
        results: List of KtapTestResult objects

    Returns:
        Dict with total, passed, failed, skipped, error counts
    """
    summary = {
        "total": len(results),
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
    }

    for r in results:
        if r.status == TestStatus.PASS:
            summary["passed"] += 1
        elif r.status == TestStatus.FAIL:
            summary["failed"] += 1
        elif r.status == TestStatus.SKIP:
            summary["skipped"] += 1
        elif r.status == TestStatus.ERROR:
            summary["errors"] += 1

    return summary
