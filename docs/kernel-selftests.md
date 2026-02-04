# Kernel Selftests Workflow Documentation

This document describes how to use the kernel selftests workflow to run Linux kernel selftests on OpenWrt devices using pytest and labgrid.

## Overview

The kernel selftests workflow allows you to:

- Run Linux kernel selftests on real OpenWrt hardware using pytest
- Test custom OpenWrt firmware images
- Get automated results posted as GitHub issue comments
- Leverage the existing labgrid testing infrastructure

## Supported Devices

Currently supported devices:

- `bananapi_bpi-r64-kernel` - Banana Pi BPI-R64 with kernel selftest support

## How to Use

### 1. Create an Issue

Create a new issue using the "Kernel Selftests Request" template, or create a regular issue and add a comment with the test request.

### 2. Format Your Test Request

In an issue comment, use this exact format:

```
/test-kernel-selftests
device: bananapi_bpi-r64-kernel
command: make -C net run_tests
firmware: https://example.com/path/to/your-firmware.bin
```

### Parameters

#### device

- **Required**: Target device identifier
- **Supported values**: `bananapi_bpi-r64-kernel`
- **Example**: `device: bananapi_bpi-r64-kernel`

#### command

- **Required**: Shell command to run in the `/root/selftests/` directory
- **Examples**:
  - `make -C net run_tests` - Run network tests
  - `make -C bpf run_tests` - Run BPF tests
  - `make -C mm run_tests` - Run memory management tests
  - `make -C filesystems run_tests` - Run filesystem tests
  - `make -C cpu-hotplug run_tests` - Run CPU hotplug tests
  - `./run_kselftest.sh` - Run all available tests
  - `./run_kselftest.sh -t net:ping` - Run specific test

#### firmware

- **Required**: Direct URL to your OpenWrt firmware image
- **Format**: Must be a direct download link
- **Supported extensions**: `.bin`, `.img`, `.gz`
- **Examples**:
  - GitHub releases: `https://github.com/user/repo/releases/download/v1.0/openwrt-image.bin`
  - File hosting: `https://example.com/firmware/custom-build.img`

## Common Test Commands

### Network Tests

```bash
# All network tests
make -C net run_tests

# Specific network test
make -C net/forwarding run_tests
```

### BPF Tests

```bash
# All BPF tests (requires BPF support in kernel)
make -C bpf run_tests

# Specific BPF test category
make -C bpf/prog_tests run_tests
```

### Memory Management Tests

```bash
# Memory management tests
make -C mm run_tests

# Specific memory test
make -C vm run_tests
```

### Filesystem Tests

```bash
# Filesystem tests
make -C filesystems run_tests

# Specific filesystem
make -C filesystems/overlayfs run_tests
```

### CPU Tests

```bash
# CPU hotplug tests
make -C cpu-hotplug run_tests

# CPU frequency tests
make -C cpufreq run_tests
```

### Run All Tests

```bash
# Run everything (may take a very long time!)
./run_kselftest.sh

# Run with specific timeout
./run_kselftest.sh -t 300  # 5 minute timeout per test
```

## Workflow Process

1. **Comment Parsing**: The workflow parses your comment and validates parameters
2. **Device Reservation**: Reserves the specified hardware device
3. **Firmware Download**: Downloads your custom firmware image
4. **Device Boot**: Boots the device with your firmware
5. **Selftests Transfer**: Downloads and transfers kernel selftests to the device
6. **Test Execution**: Runs your specified command
7. **Results Collection**: Collects and formats test output
8. **Comment Results**: Posts formatted results as a comment
9. **Cleanup**: Powers off device and releases reservation

## Results Format

The bot will comment with:

- Test summary (total/passed/failed counts)
- Detailed test output in a collapsible section
- Link to workflow logs for debugging
- Firmware and command information

Example result:

```markdown
# 🧪 Kernel Selftests Results

**Device:** bananapi_bpi-r64-kernel
**Command:** `make -C net run_tests`
**Firmware:** https://example.com/firmware.bin

## Summary

- **Total Tests:** 45
- **Passed:** 43 ✅
- **Failed:** 2 ❌

## Detailed Output

<details>
<summary>Click to expand full test output</summary>
[Test output here...]
</details>
```

## Troubleshooting

### Common Issues

1. **Invalid device**: Only `bananapi_bpi-r64-kernel` is currently supported
2. **Invalid firmware URL**: Must be a direct download link
3. **Device busy**: Device may be reserved by another test
4. **Test timeout**: Long-running tests may timeout (default: 30 minutes)
5. **Firmware boot failure**: Custom firmware may not boot properly

### Getting Help

- Check workflow logs for detailed error information
- Ensure your firmware is compatible with the target device
- Verify your firmware URL is accessible and downloads correctly
- Consider breaking large test suites into smaller commands

## Firmware Requirements

Your OpenWrt firmware should have:

- Kernel selftests support enabled
- Sufficient storage space for selftests (~100MB)
- Network connectivity (if tests require it)
- Required kernel modules for your tests

## Security Notes

- Firmware images are downloaded from user-provided URLs
- Tests run in an isolated hardware environment
- No persistent data is stored after tests complete
- Device is fully reset between test runs

## Contributing

To add support for additional devices:

1. Add device configuration to `targets/` directory
2. Update workflow device validation
3. Test with the new device configuration
4. Update this documentation

## Limitations

- Single device type currently supported
- Tests run with root privileges only
- No custom environment variables supported
- Maximum test runtime: 30 minutes
- Results limited to stdout/stderr capture
