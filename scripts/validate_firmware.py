#!/usr/bin/env python3
"""
Simple Firmware Validation Helper Script

This script helps validate OpenWrt firmware URLs before submitting them
to the kernel selftests workflow. It checks URL accessibility and basic
file properties.
"""

import sys
import requests
import argparse
from pathlib import Path


class SimpleFirmwareValidator:
    """Simple validator for OpenWrt firmware URLs"""

    SUPPORTED_EXTENSIONS = [".bin", ".img", ".gz", ".xz"]
    SUPPORTED_DEVICES = ["bananapi_bpi-r64-kernel"]
    MIN_SIZE = 1024 * 1024  # 1MB minimum
    MAX_SIZE = 512 * 1024 * 1024  # 512MB maximum

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "OpenWrt-Tests-Firmware-Validator/1.0"}
        )

    def validate_url(self, url):
        """Validate firmware URL accessibility and basic properties"""
        try:
            print(f"Checking URL: {url}")
            response = self.session.head(url, timeout=30, allow_redirects=True)

            if response.status_code != 200:
                return False, f"URL returned status {response.status_code}"

            # Check content length if available
            content_length = response.headers.get("content-length")
            if content_length:
                size = int(content_length)
                if size < self.MIN_SIZE:
                    return False, f"File too small: {size} bytes (min: {self.MIN_SIZE})"
                if size > self.MAX_SIZE:
                    return False, f"File too large: {size} bytes (max: {self.MAX_SIZE})"
                print(f"File size: {size:,} bytes ({size / (1024 * 1024):.1f} MB)")

            # Check file extension
            filename = Path(response.url).name.lower()
            if not any(filename.endswith(ext) for ext in self.SUPPORTED_EXTENSIONS):
                return (
                    False,
                    f"Unsupported file extension. Supported: {', '.join(self.SUPPORTED_EXTENSIONS)}",
                )

            print(f"Filename: {filename}")
            print(f"Content-Type: {response.headers.get('content-type', 'Unknown')}")

            return True, "URL validation passed"

        except requests.RequestException as e:
            return False, f"Failed to access URL: {str(e)}"

    def validate_device(self, device):
        """Validate device is supported"""
        if device not in self.SUPPORTED_DEVICES:
            return (
                False,
                f"Device '{device}' not supported. Supported: {', '.join(self.SUPPORTED_DEVICES)}",
            )
        return True, f"Device '{device}' is supported"


def main():
    parser = argparse.ArgumentParser(
        description="Validate OpenWrt firmware URL for kernel selftests",
        epilog="""
Example:
  python3 validate_firmware.py https://example.com/firmware.bin bananapi_bpi-r64-kernel
        """,
    )

    parser.add_argument("url", help="Firmware download URL")
    parser.add_argument("device", help="Target device name")

    args = parser.parse_args()

    validator = SimpleFirmwareValidator()

    print("=" * 60)
    print("FIRMWARE VALIDATION")
    print("=" * 60)

    # Validate device
    device_valid, device_message = validator.validate_device(args.device)
    print(f"Device: {device_message}")
    if not device_valid:
        print("❌ Device validation failed")
        sys.exit(1)

    # Validate URL
    url_valid, url_message = validator.validate_url(args.url)
    print(f"URL: {url_message}")
    if not url_valid:
        print("❌ URL validation failed")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("✅ VALIDATION PASSED")
    print("=" * 60)
    print("Your firmware URL is ready for kernel selftests!")
    print("\nExample usage:")
    print("```")
    print("/test-kernel-selftests")
    print(f"device: {args.device}")
    print("command: make -C net run_tests")
    print(f"firmware: {args.url}")
    print("```")


if __name__ == "__main__":
    main()
