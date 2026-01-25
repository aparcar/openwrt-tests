"""
Device Health Check Tool for Lab Maintainers

Standalone tool to verify devices are accessible and functioning.
Not part of KernelCI - this is for lab maintenance only.

Usage:
    python -m labgrid_kci_adapter.health_check [device_name]
    python -m labgrid_kci_adapter.health_check --all
"""

import argparse
import subprocess
import sys
from pathlib import Path

from .config import settings


def check_device(target_file: Path) -> tuple[str, bool, str]:
    """
    Run basic health check on a device.

    Returns:
        Tuple of (device_name, passed, message)
    """
    device_name = target_file.stem

    try:
        # Try to acquire and release the target via labgrid-client
        result = subprocess.run(
            [
                "labgrid-client",
                "-c",
                str(target_file),
                "acquire",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={"LG_COORDINATOR": settings.lg_coordinator},
        )

        if result.returncode != 0:
            return (device_name, False, f"Acquire failed: {result.stderr.strip()}")

        # Release immediately
        subprocess.run(
            [
                "labgrid-client",
                "-c",
                str(target_file),
                "release",
            ],
            capture_output=True,
            timeout=10,
            env={"LG_COORDINATOR": settings.lg_coordinator},
        )

        return (device_name, True, "OK")

    except subprocess.TimeoutExpired:
        return (device_name, False, "Timeout waiting for device")
    except Exception as e:
        return (device_name, False, str(e))


def list_devices(targets_dir: Path) -> list[Path]:
    """List all device target files."""
    return list(targets_dir.glob("*.yaml"))


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
        help="Directory containing target YAML files",
    )
    args = parser.parse_args()

    if not args.device and not args.all:
        parser.print_help()
        sys.exit(1)

    targets_dir = args.targets_dir
    if not targets_dir.exists():
        print(f"Error: Targets directory not found: {targets_dir}")
        sys.exit(1)

    # Get devices to check
    if args.all:
        target_files = list_devices(targets_dir)
    else:
        target_file = targets_dir / f"{args.device}.yaml"
        if not target_file.exists():
            print(f"Error: Device not found: {args.device}")
            sys.exit(1)
        target_files = [target_file]

    # Run checks
    print(f"Checking {len(target_files)} device(s)...\n")

    passed = 0
    failed = 0

    for target_file in target_files:
        name, ok, message = check_device(target_file)
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
