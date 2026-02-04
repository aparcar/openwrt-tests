---
name: Kernel Selftests Request
about: Request kernel selftests to be run on a specific device with custom firmware
title: "Kernel Selftests: [Brief description of your test]"
labels: ["kernel-selftests", "testing"]
assignees: ""
---

## Kernel Selftests Request

Use this template to request kernel selftests on real OpenWrt hardware. The tests run using pytest and labgrid infrastructure.

Please use the following format to request kernel selftests:

```
/test-kernel-selftests
device: bananapi_bpi-r64-kernel
command: make -C net run_tests
firmware: https://example.com/path/to/your-openwrt-image.bin
```

### Parameters

- **device**: Target device to run tests on
  - Currently supported: `bananapi_bpi-r64-kernel`

- **command**: Shell command to execute in the `/root/selftests/` directory
  - The device will download selftests from the internet automatically
  - Examples:
    - `make -C net run_tests` - Run networking tests
    - `make -C bpf run_tests` - Run BPF tests
    - `make -C mm run_tests` - Run memory management tests
    - `./run_kselftest.sh` - Run all available tests

- **firmware**: Direct URL to your OpenWrt firmware image
  - Must be a direct download link (e.g., GitHub releases, file hosting service)
  - Device must have internet connectivity for downloading selftests
  - Supported formats: `.bin`, `.img`, `.gz` files

### Test Description

Please describe:

- What you're testing
- Expected behavior
- Any specific configuration in your firmware

### How It Works

1. Your custom firmware is flashed to real hardware
2. Device boots and connects to the internet
3. Kernel selftests are downloaded directly on the device
4. Your specified command runs via pytest and labgrid
5. Results are automatically posted as a comment

### Additional Notes

- Tests run using the existing pytest/labgrid infrastructure
- Device needs internet connectivity to download selftests
- Results include pytest output and detailed workflow logs
- Device is automatically powered off and released after testing
