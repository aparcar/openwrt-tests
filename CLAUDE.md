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
# Via Makefile (from openwrt.git parent directory — this repo lives at openwrt.git/tests/)
make tests/x86-64 V=s              # x86-64 QEMU
make tests/malta-be V=s            # MIPS big-endian QEMU
make tests/armsr-armv8 V=s         # ARM64 QEMU
make tests/x86-64 K="test_shell"   # Filter by test name

# Direct pytest (from this repo)
pytest tests/ --lg-env targets/qemu_x86-64.yaml --lg-log --log-cli-level=CONSOLE --lg-colored-steps --firmware /path/to/firmware.bin
pytest tests/ -k "test_shell or test_ssh"  # Filter tests

# Remote device testing (requires lab access)
LG_PLACE=aparcar-openwrt_one LG_PROXY=labgrid-aparcar LG_IMAGE=<firmware-url> \
  pytest tests/ --lg-env targets/openwrt_one.yaml --log-cli-level=CONSOLE
```

### Remote Device Workflow
```bash
# Lock device before use (prevents CI/other developer conflicts)
uv run labgrid-client lock
# Boot device to shell state and access console
uv run labgrid-client --state shell console
# Run tests
pytest tests/ --log-cli-level=CONSOLE
# Unlock when done
uv run labgrid-client unlock
```

### Linting
```bash
uv run ruff check .        # Lint
uv run ruff format .       # Format
uv run isort .             # Sort imports
```

## Architecture

### Core Components

- **tests/** — pytest test suites using labgrid fixtures
- **targets/** — Device configuration YAML files (QEMU and 25+ physical devices)
- **strategies/** — Custom labgrid strategies for device boot/provisioning
- **kernelci/** — Self-hosted KernelCI infrastructure (labgrid-runner + openwrt-scheduler)
- **labnet.yaml** — Central registry of all devices, labs, and developer SSH keys
- **ansible/** — Deployment playbooks for lab infrastructure

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
- `shell_command` — Serial console access via labgrid strategy (transitions device to "shell" state)
- `ssh_command` — SSH access to device (depends on `shell_command`)

Both provide `run(cmd)` returning `(stdout_lines, stderr_lines, exit_code)` and `run_check(cmd)` that raises on non-zero exit. Example:
```python
def test_uname(shell_command):
    assert "GNU/Linux" in shell_command.run("uname -a")[0][0]

def test_echo(ssh_command):
    [output] = ssh_command.run_check("echo 'hello'")
    assert output == "hello"
```

Other fixtures and helpers:
- `ubus_call(command, namespace, method, params)` — Wraps OpenWrt's ubus JSON-RPC, returns parsed JSON
- `results_bag` — pytest-harvest fixture for collecting structured test results (e.g., board name, kernel version)
- `record_property` — Standard pytest fixture for recording test metadata
- `@pytest.mark.lg_feature("wifi")` — Marker to skip tests when a device lacks a feature (features defined in target YAML)

### labnet.yaml — Lab Federation

Central registry defining all devices, labs, and access. Key structure:
- `devices:` — Device definitions with `name`, `target` (OpenWrt build target), `firmware` (image filename), optional `snapshots_only: true`
- `labs:` — Lab definitions with `proxy` host, `maintainers`, device list, and authorized `developers`
- `developers:` — SSH public keys for lab access

Device identifiers follow the format `<lab>-<device>` (e.g., `aparcar-openwrt_one`).

### KernelCI Infrastructure

Two subprojects in `kernelci/` connect this test framework to KernelCI:

**labgrid-runner/** — Generic runner connecting any labgrid lab to KernelCI (separate Python package, Python 3.11+):
- Polls KernelCI API for test jobs (pull-mode, no inbound connections needed)
- Matches jobs against locally available labgrid devices, claims and executes them
- Parses KTAP output for kernel selftest subtest results
- Submits results back to KernelCI
- Key modules: `poller.py` (job polling), `executor.py` (test execution), `ktap_parser.py` (KTAP parsing)

**openwrt-scheduler/** — OpenWrt-specific firmware discovery and test scheduling:
- Checks OpenWrt firmware servers for latest builds and stores them in the database
- Schedules test jobs with a specific repository containing the actual tests
- Key modules: `firmware_trigger.py` (FastAPI service), `firmware_sources/` (source plugins), `test_scheduler.py`, `kcidb_bridge.py` (dashboard uploads)

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
| `FIRMWARE_VERSION` | Expected firmware version (validated by `test_firmware_version`) |

## Dependencies

- Python 3.13+ (main test framework); Python 3.11+ (labgrid-runner)
- labgrid from custom fork: `github.com/aparcar/labgrid.git` (branch: aparcar/staging)
- QEMU packages: qemu-system-mips, qemu-system-x86, qemu-system-aarch64

## Code Style

- Line length: 88 (black-compatible)
- Linter: ruff (E, F, I, W rules)
- Import sorting: isort with black profile
- First-party modules: `openwrt_scheduler`, `labgrid_runner`
- CI runs lint checks on Python 3.11, 3.12, 3.13 via `formal.yml`

## CI/CD Workflows (.github/workflows/)

- **daily.yml** — Daily matrix testing across snapshot + stable releases on all devices; dynamically generates matrix from `labnet.yaml`; excludes devices with open healthcheck issues
- **pull_requests.yml** — PR validation on QEMU targets only (fast feedback)
- **healthcheck.yml** — Device health monitoring (24h); auto-creates/closes GitHub issues for failing devices
- **kernel-selftests.yml** — On-demand kernel selftest execution triggered by `/test-kernel-selftests` in issue comments
- **formal.yml** — Linting and formatting checks (ruff, isort) on push/PR
