# Kselftest Integration

This directory contains pytest wrappers for running Linux kernel selftests (kselftests) on OpenWrt devices.

## Overview

Kselftests are the Linux kernel's built-in test suite. On OpenWrt, they are packaged as `kselftests-*` packages and installed to `/usr/libexec/kselftest/`.

The pytest wrappers in this directory:
1. Run kselftest binaries on the target device via labgrid
2. Capture KTAP output from stdout
3. The executor parses KTAP to report individual subtest results to KernelCI

## KTAP Format

Kselftests output results in [KTAP (Kernel Test Anything Protocol)](https://docs.kernel.org/dev-tools/ktap.html) format:

```
KTAP version 1
1..3
ok 1 - test_socket_create
not ok 2 - test_socket_bind # SKIP requires CAP_NET_RAW
ok 3 - test_socket_listen
```

Key elements:
- `ok N` = test passed
- `not ok N` = test failed
- `# SKIP reason` = test was skipped
- Nested subtests use 2-space indentation

## Files

- `conftest.py` - Pytest fixtures for running kselftests
- `test_kselftest.py` - Test functions for each kselftest subsystem

## Fixtures

### `kselftest_runner`

Runs an entire kselftest subsystem:

```python
def test_kselftest_net(kselftest_runner):
    output = kselftest_runner("net", timeout=1800)
    # KTAP output is printed to stdout and captured by executor
```

### `kselftest_single`

Runs a single kselftest binary:

```python
def test_specific(kselftest_single):
    output = kselftest_single("net", "reuseport_bpf", timeout=300)
```

## Test Plan Mapping

Each kselftest subsystem has a corresponding test plan in `pipeline.yaml`:

| Test Plan | Pytest Test | Kselftest Subsystem |
|-----------|-------------|---------------------|
| `kselftest_net` | `test_kselftest_net` | `/usr/libexec/kselftest/net/` |
| `kselftest_timers` | `test_kselftest_timers` | `/usr/libexec/kselftest/timers/` |
| `kselftest_rtc` | `test_kselftest_rtc` | `/usr/libexec/kselftest/rtc/` |
| ... | ... | ... |

## How Results Flow

```
Target Device                    Host                         KernelCI
┌─────────────────┐    ┌──────────────────────┐    ┌─────────────────┐
│ /usr/libexec/   │    │ pytest + labgrid     │    │ API             │
│ kselftest/net/  │───>│ captures stdout      │───>│ receives test   │
│ (KTAP output)   │    │ (KTAP in stdout)     │    │ nodes           │
└─────────────────┘    └──────────────────────┘    └─────────────────┘
                              │
                              ▼
                       ┌──────────────────────┐
                       │ executor.py          │
                       │ _try_parse_ktap()    │
                       │ - detects KTAP       │
                       │ - parses subtests    │
                       │ - creates TestResult │
                       │   per subtest        │
                       └──────────────────────┘
```

## Adding a New Kselftest Subsystem

1. Add the kselftest package to `test_types.py` IMAGE_PROFILES
2. Add a test plan to `pipeline.yaml`
3. Add a test function to `test_kselftest.py`:

```python
class TestKselftestNewSubsystem:
    def test_kselftest_newsubsystem(self, kselftest_runner):
        output = kselftest_runner("newsubsystem", timeout=300)
```

## Troubleshooting

### No KTAP output parsed

Check the console log for warnings like:
```
WARNING: Kselftest 'net' output doesn't look like KTAP format
```

This means the kselftest ran but didn't produce parseable output. Possible causes:
- Kselftest package not installed correctly
- Test crashed before producing output
- Test uses non-standard output format

### Test times out

Increase the timeout in the test function:
```python
output = kselftest_runner("net", timeout=3600)  # 1 hour
```

Also update `pipeline.yaml` timeout for the test plan.

### Subsystem not found

The test will be skipped with:
```
SKIPPED: Kselftest subsystem 'net' not installed
```

Ensure the `kselftests-net` package is included in the firmware image profile.

## Required Packages

The `kselftest` image profile in `test_types.py` includes:
- `kselftests-net`
- `kselftests-timers`
- `kselftests-rtc`
- `kselftests-clone3`
- `kselftests-openat2`
- `kselftests-exec`
- `kselftests-mincore`
- `kselftests-splice`
- `kselftests-sync`
- `kselftests-futex`
- `kselftests-mqueue`
- `kselftests-sigaltstack`
- `kselftests-kcmp`
- `kselftests-size`

Plus dependencies like `bash`, `iproute2-full`, etc.

## Device Capabilities

Kselftest jobs require:
- `serial_console` - Device must have serial console access
- `isolated_network` - Required for network tests (prevents interference)

Configure these in your lab's device definitions.
