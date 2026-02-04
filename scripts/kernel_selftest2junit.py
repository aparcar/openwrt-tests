#!/usr/bin/env python3
import argparse
import sys
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom
from collections import defaultdict

TEST_RE = re.compile(r"^TEST:\s*(.+?)\s+\[\s*([A-Z]+)\s*\]\s*$")
INFO_RE = re.compile(r"^INFO:\s*(?:#\s*)?(.*\S)\s*$")
INDENTED_RE = re.compile(r"^\s+(.*\S.*)$")

STATUS_MAP = {
    "OK": "pass",
    "FAIL": "fail",
    "XFAIL": "xfail",
    "SKIP": "skip",
}


def parse_selftest(lines):
    """
    Parse kernel selftest textual output into a structure:
    { suite_name: [ {name, status, details} ] }
    """
    suites = defaultdict(list)
    current_suite = "default"
    current_test = None

    for raw in lines:
        line = raw.rstrip("\n")

        # Section / suite header
        m = INFO_RE.match(line)
        if m:
            header = m.group(1).strip()
            if header:
                current_suite = header
            current_test = None
            continue

        # Test line
        m = TEST_RE.match(line)
        if m:
            name, status = m.group(1).strip(), m.group(2).strip().upper()
            status = STATUS_MAP.get(status, "pass")  # default to pass if unknown
            current_test = {"name": name, "status": status, "details": []}
            suites[current_suite].append(current_test)
            continue

        # Indented continuation: treat as details for the last test
        m = INDENTED_RE.match(line)
        if m and current_test is not None:
            current_test["details"].append(m.group(1))
            continue

        # Anything else is ignored for now

    # Join details into a single string
    for tests in suites.values():
        for t in tests:
            t["details"] = "\n".join(t["details"]).strip()

    return suites


def suites_to_junit_xml(suites, top_name="kernel-selftests"):
    """
    Convert parsed suites/tests to JUnit XML (<testsuites> root).
    """
    root = ET.Element("testsuites")
    root.set("name", top_name)

    for suite_name, tests in suites.items():
        ts = ET.SubElement(root, "testsuite", name=suite_name)
        # Collect counts
        total = len(tests)
        failures = sum(1 for t in tests if t["status"] == "fail")
        skipped = sum(1 for t in tests if t["status"] in ("skip", "xfail"))
        errors = 0

        ts.set("tests", str(total))
        ts.set("failures", str(failures))
        ts.set("errors", str(errors))
        ts.set("skipped", str(skipped))
        ts.set("time", "0")

        for idx, t in enumerate(tests, 1):
            case = ET.SubElement(
                ts, "testcase", classname=suite_name, name=t["name"], time="0"
            )
            if t["status"] == "fail":
                # Put a short message + details body
                msg = t["details"].splitlines()[0] if t["details"] else "failed"
                fail = ET.SubElement(case, "failure", message=msg)
                if t["details"]:
                    fail.text = t["details"]
            elif t["status"] in ("skip", "xfail"):
                reason = "xfail" if t["status"] == "xfail" else "skipped"
                ET.SubElement(case, "skipped", message=reason)
            # For passes, no child elements

    return root


def pretty_print_xml(elem):
    rough = ET.tostring(elem, encoding="utf-8")
    reparsed = minidom.parseString(rough)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(
        description="Convert Linux kernel selftest text output to JUnit XML."
    )
    ap.add_argument(
        "input", nargs="?", default="-", help="Input file (default: stdin)."
    )
    ap.add_argument(
        "-o", "--output", default="-", help="Output file (default: stdout)."
    )
    ap.add_argument(
        "--suite-name", default="kernel-selftests", help="Top-level testsuites name."
    )
    args = ap.parse_args()

    if args.input == "-" or args.input == "":
        data = sys.stdin.read()
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            data = f.read()

    suites = parse_selftest(data.splitlines())
    xml_root = suites_to_junit_xml(suites, top_name=args.suite_name)
    xml_bytes = pretty_print_xml(xml_root)

    if args.output == "-" or args.output == "":
        sys.stdout.buffer.write(xml_bytes)
    else:
        with open(args.output, "wb") as f:
            f.write(xml_bytes)


if __name__ == "__main__":
    main()
