"""
Device Health Check Tool for Lab Maintainers

Standalone tool to verify devices are accessible and functioning.
Not part of KernelCI - this is for lab maintenance only.

Usage:
    python -m labgrid_runner.health_check [device_name]
    python -m labgrid_runner.health_check --all
"""

import argparse
import subprocess
import sys
from pathlib import Path

from .config import settings


def check_device(device_name: str) -> tuple[str, bool, str]:
    """
    Run basic health check on a device using place-based acquisition.

    Uses labgrid-client -p <place> to check if the device is accessible
    via the coordinator, without needing to parse target config files.

    Returns:
        Tuple of (device_name, passed, message)
    """
    import os

    try:
        env = os.environ.copy()
        env["LG_COORDINATOR"] = settings.lg_coordinator

        # Construct place name from lab name and device
        # Lab name already includes the full prefix (e.g., "labgrid-aparcar")
        place_name = f"{settings.lab_name}-{device_name}"

        # Try to acquire the place
        result = subprocess.run(
            [
                "labgrid-client",
                "-p",
                place_name,
                "acquire",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        if result.returncode != 0:
            return (device_name, False, f"Acquire failed: {result.stderr.strip()}")

        # Release immediately
        subprocess.run(
            [
                "labgrid-client",
                "-p",
                place_name,
                "release",
            ],
            capture_output=True,
            timeout=10,
            env=env,
        )

        return (device_name, True, "OK")

    except subprocess.TimeoutExpired:
        return (device_name, False, "Timeout waiting for device")
    except Exception as e:
        return (device_name, False, str(e))


def list_devices_from_targets(targets_dir: Path) -> list[str]:
    """List device names from target YAML files."""
    return [f.stem for f in targets_dir.glob("*.yaml")]


def list_devices_from_coordinator() -> list[str]:
    """List device names from labgrid coordinator places."""
    import os

    env = os.environ.copy()
    env["LG_COORDINATOR"] = settings.lg_coordinator

    result = subprocess.run(
        ["labgrid-client", "places"],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    if result.returncode != 0:
        return []

    # Parse place names and extract device names for this lab
    # Place format: {lab_name}-{device_name} (lab_name includes full prefix)
    prefix = f"{settings.lab_name}-"
    devices = []
    for line in result.stdout.strip().split("\n"):
        place = line.strip()
        if place.startswith(prefix):
            device = place[len(prefix) :]
            devices.append(device)
    return devices


def main():
    parser = argparse.ArgumentParser(
        description="Device health check for lab maintainers"
    )
    parser.add_argument("device", nargs="?", help="Device name to check")
    parser.add_argument("--all", action="store_true", help="Check all devices")
    parser.add_argument(
        "--targets-dir",
        type=Path,
        default=settings.targets_dir,
        help="Directory containing target YAML files (optional)",
    )
    args = parser.parse_args()

    if not args.device and not args.all:
        parser.print_help()
        sys.exit(1)

    # Get devices to check
    if args.all:
        # First try to get devices from coordinator
        devices = list_devices_from_coordinator()
        if not devices:
            # Fall back to target files
            targets_dir = args.targets_dir
            if targets_dir.exists():
                devices = list_devices_from_targets(targets_dir)
            else:
                print("Error: No devices found from coordinator or targets directory")
                sys.exit(1)
    else:
        devices = [args.device]

    # Run checks
    print(f"Checking {len(devices)} device(s)...\n")

    passed = 0
    failed = 0

    for device in devices:
        name, ok, message = check_device(device)
        status = "✓" if ok else "✗"
        print(f"  {status} {name}: {message}")

        if ok:
            passed += 1
        else:
            failed += 1

    # Summary
    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
