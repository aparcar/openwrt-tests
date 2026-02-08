# Labgrid Runner

A generic runner connecting [labgrid](https://labgrid.readthedocs.io/) test
infrastructure to [KernelCI](https://kernelci.org/) for automated hardware testing.

**This is a reusable component** - while developed for OpenWrt testing, it can be
used by any project that wants to connect labgrid-managed devices to KernelCI.

## Features

- **Pull-mode architecture**: Labs poll KernelCI API for jobs (no inbound connections)
- **Pytest integration**: Executes tests via pytest with labgrid plugin
- **Automatic test sync**: Pulls tests from git before each job
- **Health checks**: Automatic device health monitoring
- **Result collection**: Submits results as KernelCI test nodes

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    KernelCI API (Central)                   │
│                  - Job queue (nodes)                        │
│                  - Result storage                           │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │ HTTPS (poll)
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 Labgrid Runner (Lab)                        │
│  ┌─────────┐  ┌──────────┐  ┌────────────┐  ┌───────────┐  │
│  │ Poller  │→ │ Executor │→ │ Test Sync  │→ │ Labgrid   │  │
│  └─────────┘  └──────────┘  └────────────┘  └───────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ gRPC
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Labgrid Coordinator + Devices                  │
│         (Router, SBC, QEMU, etc.)                          │
└─────────────────────────────────────────────────────────────┘
```

## Installation

```bash
pip install labgrid-runner
# or
docker pull ghcr.io/openwrt/labgrid-runner
```

## Configuration

All configuration via environment variables:

```bash
# Required
LAB_NAME=my-lab                    # Unique lab identifier
KCI_API_URL=https://api.kci.org    # KernelCI API endpoint
KCI_API_TOKEN=your-token           # API authentication token
LG_COORDINATOR=localhost:20408     # Labgrid coordinator address

# Tests (pulled before each job)
TESTS_REPO_URL=https://github.com/your/tests.git
TESTS_REPO_BRANCH=main

# Optional
POLL_INTERVAL=30                   # Seconds between job polls
MAX_CONCURRENT_JOBS=3              # Parallel job limit
HEALTH_CHECK_INTERVAL=86400        # Health check interval (24h)
HEALTH_CHECK_ENABLED=true          # Enable automatic health checks

# Storage (optional)
MINIO_ENDPOINT=storage.example.com:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_LOGS_BUCKET=test-logs        # Bucket for test logs
```

## Usage

### Docker Compose

```yaml
services:
  labgrid-runner:
    image: ghcr.io/openwrt/labgrid-runner
    environment:
      LAB_NAME: my-lab
      KCI_API_URL: https://api.kernelci.org
      KCI_API_TOKEN: ${KCI_API_TOKEN}
      LG_COORDINATOR: labgrid-coordinator:20408
      TESTS_REPO_URL: https://github.com/your/tests.git
    volumes:
      - ./targets:/app/targets:ro   # Labgrid target configs
    depends_on:
      - labgrid-coordinator
```

### Standalone

```bash
export LAB_NAME=my-lab
export KCI_API_URL=https://api.kernelci.org
export KCI_API_TOKEN=your-token
export LG_COORDINATOR=localhost:20408
export TESTS_REPO_URL=https://github.com/your/tests.git

python -m labgrid_runner.service
```

## Target Configuration

Place labgrid target YAML files in the `targets/` directory:

```yaml
# targets/my-device.yaml
targets:
  main:
    resources:
      RemotePlace:
        name: my-device
    drivers:
      ShellDriver:
        prompt: 'root@.*:'
        login_prompt: 'login:'
        username: root
```

## Job Format

The adapter expects KernelCI job nodes with:

```json
{
  "kind": "job",
  "state": "available",
  "data": {
    "device_type": "my-device",
    "test_plan": "base",
    "tests": ["test_boot", "test_network"],
    "timeout": 1800,
    "firmware_url": "https://...",
    "tests_repo": "https://github.com/...",
    "tests_branch": "main"
  }
}
```

## Test Structure

Tests are standard pytest files using the labgrid plugin:

```python
import pytest

def test_device_boots(target):
    """Test that device boots successfully."""
    shell = target.get_driver("ShellDriver")
    shell.run_check("uname -a")

def test_network(target):
    """Test network connectivity."""
    shell = target.get_driver("ShellDriver")
    shell.run_check("ping -c 3 8.8.8.8")
```

## Using with Other Projects

This adapter is project-agnostic. To use with your project:

1. Set up a KernelCI instance (or use the public one)
2. Create test jobs with your device types
3. Configure the adapter with your tests repository
4. Create labgrid target files for your devices

The adapter will:
- Poll for jobs matching your device types
- Clone/update your tests repository
- Execute tests via pytest + labgrid
- Submit results to KernelCI

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check .
ruff format .
```

## License

See repository root for license information.
