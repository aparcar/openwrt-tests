# Kernel Selftests Workflow

This repository includes a GitHub Actions workflow for running Linux kernel selftests on real OpenWrt hardware devices with custom firmware images. The workflow leverages pytest and labgrid for robust device testing.

## 🚀 Quick Start

1. **Create an Issue**: Open a new issue or use an existing one
2. **Add a Comment** with your test request in this format:

```
/test-kernel-selftests
device: bananapi_bpi-r64-kernel
command: make -C net run_tests
firmware: https://example.com/path/to/your-openwrt-firmware.bin
```

3. **Wait for Results**: The bot will automatically run your tests and post results as a comment

## 📋 Parameters

### Required Parameters

| Parameter  | Description                          | Example                                                            |
| ---------- | ------------------------------------ | ------------------------------------------------------------------ |
| `device`   | Target hardware device               | `bananapi_bpi-r64-kernel`                                          |
| `command`  | Command to run in `/root/selftests/` | `make -C net run_tests`                                            |
| `firmware` | Direct URL to OpenWrt firmware       | `https://github.com/user/repo/releases/download/v1.0/firmware.bin` |

### Supported Devices

Currently supported devices:

- `bananapi_bpi-r64-kernel` - Banana Pi BPI-R64 with kernel selftest support

## 🧪 Common Test Commands

### Network Tests

```bash
# All network tests
make -C net run_tests

# Specific network subsystem
make -C net/forwarding run_tests
make -C net/mptcp run_tests
```

### BPF Tests

```bash
# All BPF tests (requires BPF support in kernel)
make -C bpf run_tests

# Specific BPF categories
make -C bpf/prog_tests run_tests
make -C bpf/verifier run_tests
```

### Memory Management

```bash
# Memory management tests
make -C mm run_tests
make -C vm run_tests
```

### Filesystems

```bash
# All filesystem tests
make -C filesystems run_tests

# Specific filesystem
make -C filesystems/overlayfs run_tests
make -C filesystems/binderfs run_tests
```

### CPU and Power Management

```bash
# CPU hotplug tests
make -C cpu-hotplug run_tests

# CPU frequency tests
make -C cpufreq run_tests

# Power management
make -C powercap run_tests
```

### Security

```bash
# Security tests
make -C seccomp run_tests
make -C capabilities run_tests
```

### Run Everything

```bash
# Run all available tests (may take hours!)
./run_kselftest.sh

# Run with timeout per test
./run_kselftest.sh -t 300
```

## 📦 Firmware Requirements

Your OpenWrt firmware image should have:

### Essential Requirements

- **Kernel selftests support**: Enable `CONFIG_SAMPLES` and relevant test configs
- **Root filesystem**: Writable root filesystem with sufficient space (~200MB)
- **Shell access**: Working shell environment (ash/bash)
- **Basic utilities**: tar, gzip, make, gcc (for tests that need compilation)

### Recommended Kernel Configs

```
CONFIG_SAMPLES=y
CONFIG_SAMPLE_SECCOMP=y
CONFIG_NET_SCH_NETEM=y
CONFIG_TUN=y
CONFIG_NAMESPACES=y
CONFIG_USER_NS=y
CONFIG_NET_NS=y
CONFIG_PID_NS=y
```

### File Format Support

- `.bin` - Raw binary images
- `.img` - Disk image files
- `.gz` - Gzipped firmware images
- `.xz` - XZ compressed images

## 🔧 Validation Tools

### Validate Before Testing

Use our validation script to check your firmware before submitting:

```bash
# Quick validation (no download)
python3 scripts/validate_firmware.py --quick \
  https://example.com/firmware.bin \
  bananapi_bpi-r64-kernel

# Full validation with download and analysis
python3 scripts/validate_firmware.py \
  https://example.com/firmware.bin \
  bananapi_bpi-r64-kernel \
  --report validation-report.md
```

## 📊 Understanding Results

### Result Format

The bot posts structured results including:

```markdown
# 🧪 Kernel Selftests Results

**Status:** ✅ COMPLETED
**Device:** bananapi_bpi-r64-kernel
**Command:** `make -C net run_tests`

## Summary

- **Total Tests:** 3
- **Passed:** 3 ✅
- **Failed:** 0 ❌
- **Errors:** 0 💥

## Test Details

The kernel selftests were executed using pytest with labgrid. Check the workflow logs for detailed output including the complete selftest results.
```

### Status Icons

- ✅ **Passed** - Test completed successfully
- ❌ **Failed** - Test failed or had errors
- ⏭️ **Skipped** - Test was skipped (missing dependencies)
- ⏰ **Timeout** - Test exceeded time limit
- 💥 **Error** - Fatal error during test execution

## 🏗️ Workflow Architecture

### Process Flow

1. **Comment Parsing**: Extract device, command, and firmware URL
2. **Validation**: Check parameters and device availability
3. **Device Reservation**: Lock hardware device exclusively
4. **Firmware Download**: Download and verify firmware image
5. **Pytest Execution**: Run `test_kernel_selftests.py` using labgrid
6. **Device Setup**: Boot device and download selftests via internet
7. **Test Execution**: Run specified command on device
8. **Result Collection**: Pytest captures results and generates reports
9. **Cleanup**: Power down device and release lock

### Key Components

- **GitHub Actions Workflow**: `.github/workflows/kernel-selftests.yml`
- **Pytest Test**: `tests/test_kernel_selftests.py`
- **Issue Template**: `.github/ISSUE_TEMPLATE/kernel-selftests.md`
- **Firmware Validator**: `scripts/validate_firmware.py`
- **Device Config**: `targets/bananapi_bpi-r64-kernel.yaml`

## 🚨 Troubleshooting

### Common Issues

| Issue                    | Cause                       | Solution                               |
| ------------------------ | --------------------------- | -------------------------------------- |
| Invalid device           | Unsupported device name     | Use `bananapi_bpi-r64-kernel`          |
| Firmware download failed | URL inaccessible or invalid | Validate URL with validation script    |
| Boot timeout             | Firmware incompatible       | Check kernel configs and architecture  |
| Test command failed      | Missing dependencies        | Ensure required kernel modules/configs |
| Device busy              | Another test in progress    | Wait for completion or check queue     |

### Getting Help

1. **Check Workflow Logs**: Click the workflow run link in results
2. **Validate Firmware**: Use `validate_firmware.py` script
3. **Review Requirements**: Ensure kernel configs are correct
4. **Test Locally**: Try your test command on a local OpenWrt setup

### Debug Tips

```bash
# Test basic device connectivity first
command: echo "Device is working"

# Check available test suites
command: find /root/selftests -name Makefile | head -10

# Run a simple test first
command: make -C filesystems/binderfs run_tests
```

## 🔒 Security & Limitations

### Security Considerations

- Firmware downloaded from user-provided URLs
- Tests run with root privileges on isolated hardware
- No persistent data stored after tests
- Device fully reset between test runs

### Current Limitations

- Single device type supported
- Maximum test runtime: 30 minutes per pytest test
- Results processed through pytest framework
- Device must have internet connectivity for downloading selftests

## 🎯 Examples

### Basic Network Testing

```
/test-kernel-selftests
device: bananapi_bpi-r64-kernel
command: make -C net run_tests
firmware: https://github.com/myuser/openwrt-builds/releases/download/v1.0/openwrt-r64-sysupgrade.bin
```

### BPF Development Testing

```
/test-kernel-selftests
device: bananapi_bpi-r64-kernel
command: make -C bpf/prog_tests run_tests
firmware: https://downloads.example.com/openwrt-r64-bpf-enabled.img.gz
```

### Custom Test Subset

```
/test-kernel-selftests
device: bananapi_bpi-r64-kernel
command: cd net && ./run_tests.sh -t bridge,vlan,tunnel
firmware: https://my-cdn.example.com/firmware/openwrt-r64-kernel-selftests.bin
```

## 🛠️ Development

### Adding New Devices

1. Create device configuration in `targets/new-device.yaml`
2. Update workflow validation in `kernel-selftests.yml`
3. Add device to validation scripts
4. Test with sample firmware
5. Update documentation

### Extending Tests

The pytest test in `tests/test_kernel_selftests.py` can be extended to:

- Add pre-test validation steps
- Support different test frameworks
- Add post-test analysis
- Integrate with existing pytest fixtures

### Contributing

1. Fork the repository
2. Create a feature branch
3. Test your changes thoroughly
4. Submit a pull request with clear description
5. Update documentation as needed

## 📚 Additional Resources

- [Linux Kernel Selftests Documentation](https://www.kernel.org/doc/html/latest/dev-tools/kselftest.html)
- [OpenWrt Build System](https://openwrt.org/docs/guide-developer/toolchain/use-buildsystem)
- [Labgrid Documentation](https://labgrid.readthedocs.io/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)

## 📄 License

This workflow is part of the OpenWrt testing infrastructure and follows the same licensing terms as the main project.
