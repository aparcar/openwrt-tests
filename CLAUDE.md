# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenWrt Testing is a pytest-based framework for automated testing of OpenWrt firmware on real hardware devices and QEMU emulators. It uses labgrid for device control and supports 30+ physical devices across distributed labs.

## Key Commands

### Setup
```bash
uv sync                    # Install dependencies
make tests/setup V=s       # Verify installation (checks uv, qemu)
```

### Running Tests
```bash
# Via Makefile (from openwrt.git parent directory)
make tests/x86-64 V=s              # x86-64 QEMU
make tests/malta-be V=s            # MIPS big-endian QEMU
make tests/armsr-armv8 V=s         # ARM64 QEMU
make tests/x86-64 K="test_shell"   # Filter by test name

# Direct pytest
pytest tests/ --lg-env targets/qemu_x86-64.yaml --lg-log --log-cli-level=CONSOLE --lg-colored-steps --firmware /path/to/firmware.bin
pytest tests/ -k "test_shell or test_ssh"  # Filter tests

# Remote device testing (requires lab access)
LG_PLACE=<lab>-<device> LG_PROXY=<proxy-host> LG_IMAGE=<firmware-url> \
  pytest tests/ --lg-env targets/<device>.yaml
```

### Linting
```bash
uv run ruff check .        # Lint
uv run ruff format .       # Format
uv run isort .             # Sort imports
```

## Architecture

### Core Components

- **tests/** - pytest test suites using labgrid fixtures
- **targets/** - Device configuration YAML files (30+ devices: QEMU, routers, SBCs)
- **strategies/** - Custom labgrid strategies for device boot/provisioning
- **kernelci/** - Self-hosted KernelCI infrastructure (see below)
- **ansible/** - Deployment playbooks for lab infrastructure

### Labgrid Strategies

Three custom strategies handle different device boot flows:

| Strategy | File | Use Case |
|----------|------|----------|
| `QEMUNetworkStrategy` | `strategies/qemunetworkstrategy.py` | QEMU VMs with SSH port forwarding |
| `UBootTFTPStrategy` | `strategies/tftpstrategy.py` | Physical devices via U-Boot + TFTP |
| `SDMuxStrategy` | `strategies/sdmuxstrategy.py` | SD card mux-based provisioning |

Strategies implement `transition(state)` to move devices through states: `off` → `uboot` → `shell`.

### Test Fixtures (tests/conftest.py)

Tests use two primary fixtures:
- `shell_command` - Serial console access via labgrid strategy
- `ssh_command` - SSH access to device

Both provide `run(cmd)` returning (stdout, stderr, exit_code) and `run_check(cmd)` that raises on non-zero exit. Example:
```python
def test_uname(shell_command):
    assert "GNU/Linux" in shell_command.run("uname -a")[0][0]

def test_echo(ssh_command):
    [output] = ssh_command.run_check("echo 'hello'")
    assert output == "hello"
```

Helper function `ubus_call(command, namespace, method, params)` wraps OpenWrt's ubus JSON-RPC.

### KernelCI Infrastructure

Two subprojects in `kernelci/` connect this test framework to KernelCI:

**labgrid-adapter/** - Generic adapter connecting any labgrid lab to KernelCI:
- Polls KernelCI API for test jobs (pull-mode, no inbound connections)
- Executes tests via pytest with labgrid plugin
- Parses KTAP output for kernel selftest subtest results
- Submits results back to KernelCI
- Key modules: `poller.py` (job polling), `executor.py` (test execution), `ktap_parser.py` (KTAP parsing)

**openwrt-pipeline/** - OpenWrt-specific firmware pipeline:
- Watches firmware sources (official releases, GitHub PRs, custom uploads)
- Creates firmware entries in KernelCI API
- Schedules tests based on device/firmware compatibility
- Key modules: `firmware_trigger.py` (FastAPI service), `firmware_sources/` (source plugins), `test_scheduler.py`

### Device Control Flow

1. GitHub Actions or local Makefile triggers test
2. Firmware downloaded/specified via `--firmware` option or `LG_IMAGE` env var
3. labgrid target config loaded from `targets/*.yaml`
4. Strategy transitions device: power on → boot → shell ready
5. pytest executes tests with `shell_command`/`ssh_command` fixtures
6. Results collected via `results_bag` fixture or uploaded to KernelCI

### Environment Variables

| Variable | Description |
|----------|-------------|
| `LG_ENV` | Path to target YAML config |
| `LG_QEMU_BIN` | QEMU binary path |
| `LG_IMAGE` | Firmware path/URL for remote device testing |
| `LG_PLACE` | Remote device identifier (format: `<lab>-<device>`) |
| `LG_PROXY` | Lab proxy host for remote access |
| `LG_COORDINATOR` | Labgrid coordinator address (for remote labs) |

## Dependencies

- Python 3.13+
- labgrid from custom fork: `github.com/aparcar/labgrid.git` (branch: aparcar/staging)
- QEMU packages: qemu-system-mips, qemu-system-x86, qemu-system-aarch64

## Code Style

- Line length: 88 (black-compatible)
- Linter: ruff (E, F, I, W rules)
- Import sorting: isort with black profile
- First-party modules: `openwrt_pipeline`, `labgrid_kci_adapter`

## CI/CD Workflows (.github/workflows/)

- **daily.yml** - Daily matrix testing across snapshot + stable releases on all devices
- **pull_requests.yml** - PR validation on QEMU targets only
- **healthcheck.yml** - Device health monitoring (24h checks)
- **kernel-selftests.yml** - On-demand kernel selftest execution via issue comments
