# OpenWrt KernelCI - Self-Hosted Testing Infrastructure

This directory contains the Docker Compose stack for running a self-hosted
KernelCI instance tailored for OpenWrt firmware testing.

## Modular Architecture

The system is split into two components:

| Component | Description | Reusable? |
|-----------|-------------|-----------|
| `labgrid-runner/` | Generic runner connecting labgrid to KernelCI | **Yes** - usable by any project |
| `openwrt-scheduler/` | OpenWrt-specific firmware discovery and scheduling | No - OpenWrt specific |

The **labgrid-runner** is designed to be project-agnostic and can be used
by other projects that want to connect labgrid-managed devices to KernelCI.
See `labgrid-runner/README.md` for details.

## Overview

The stack provides:

- **KernelCI API (Maestro)** - Job management and REST API
- **Dashboard** - Web-based result visualization
- **Pipeline Services** - Firmware triggers, scheduling, health checks
- **Storage** - MinIO for artifacts, MongoDB for data, Redis for events
- **Reverse Proxy** - Traefik with automatic TLS certificates

## Quick Start

### Prerequisites

- Docker Engine 24.0+
- Docker Compose v2.20+
- A domain name pointing to your server (for TLS)
- At least 4GB RAM, 20GB disk space

### Installation

1. **Clone and configure:**

   ```bash
   cd kernelci
   cp .env.example .env
   ```

2. **Edit `.env` with your settings:**

   ```bash
   # Generate secure passwords
   openssl rand -base64 32  # For MONGO_PASSWORD
   openssl rand -base64 32  # For MINIO_SECRET_KEY
   openssl rand -base64 48  # For KCI_SECRET_KEY
   ```

3. **Start the stack:**

   ```bash
   docker compose up -d
   ```

4. **Check logs:**

   ```bash
   docker compose logs -f
   ```

5. **Access the services:**

   - Dashboard: `https://your-domain.org`
   - API: `https://api.your-domain.org`
   - Storage Console: `https://storage.your-domain.org`
   - Traefik Dashboard: `http://your-server:8080`

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Traefik (Reverse Proxy)                      │
│                    :80 (redirect) → :443 (TLS)                       │
└─────────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│    Dashboard    │  │   KernelCI API  │  │   MinIO Console │
│   (KernelCI)    │  │   (Maestro)     │  │   (S3 Storage)  │
│    :3000        │  │   :8001         │  │   :9001         │
└─────────────────┘  └─────────────────┘  └─────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│    MongoDB      │  │     Redis       │  │     MinIO       │
│    (Data)       │  │   (Pub/Sub)     │  │   (Artifacts)   │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

## Pipeline Services

### Firmware Trigger (`pipeline-trigger`)

Watches for new firmware from configured sources:

- **Official releases** - downloads.openwrt.org
- **GitHub PRs** - Artifacts from PR CI runs
- **Custom uploads** - Via API endpoint

### Test Scheduler (`pipeline-scheduler`)

Creates test job nodes for available firmware based on:

- Device compatibility (target/subtarget/profile)
- Device features (wifi, wan_port, etc.)
- Test plan requirements

## Lab Integration

Labs connect using the **pull-mode** architecture:

1. Lab runs the `labgrid-runner` service
2. Adapter polls API for pending jobs (`kind=job`, `state=available`)
3. Jobs are claimed by setting `state=running`
4. Tests run via pytest with labgrid plugin
5. Results submitted as test nodes under job
6. Health checks run automatically every 24 hours

See `labgrid-runner/` for the lab-side component.

### Test Execution

Tests are executed using pytest's programmatic API with the labgrid plugin.
Following the [LAVA pattern](https://docs.lavasoftware.org/lava/writing-tests.html),
tests are pulled from git before each job execution.

**Configuration:**

```bash
# Configure the tests repository (pulled before each job)
TESTS_REPO_URL=https://github.com/openwrt/openwrt-tests.git
TESTS_REPO_BRANCH=main
```

**Per-job override:**

Jobs can specify a different tests repository:

```yaml
data:
  tests_repo: "https://github.com/custom/tests.git"
  tests_branch: "feature-branch"
  tests: ["test_boot", "test_wifi"]
```

The executor:
1. Pulls tests from git (clones if not exists, updates if exists)
2. Runs pytest with labgrid plugin
3. Collects results via `ResultCollectorPlugin`
4. Submits results as KernelCI test nodes

Labgrid handles firmware flashing via its pytest fixtures.

### Lab Configuration

```bash
# Required environment variables
LAB_NAME=my-lab
KCI_API_URL=https://api.kernelci.example.com
KCI_API_TOKEN=<your-token>
LG_COORDINATOR=labgrid-coordinator:20408

# Optional - polling and concurrency
POLL_INTERVAL=30
MAX_CONCURRENT_JOBS=3

# Optional - health checks
HEALTH_CHECK_INTERVAL=86400  # 24 hours
HEALTH_CHECK_ENABLED=true

# Optional - tests repository (pulled before each job)
TESTS_REPO_URL=https://github.com/openwrt/openwrt-tests.git
TESTS_REPO_BRANCH=main
```

### Health Checks

The adapter runs automatic health checks:

- Every 24 hours (configurable via `HEALTH_CHECK_INTERVAL`)
- Failing devices removed from job pool
- Recovered devices automatically re-added
- Results logged for lab maintainers

Manual check:
```bash
python -m labgrid_runner.health_check --all
```

## Configuration

### `config/pipeline.yaml`

Main pipeline configuration including:

- Firmware sources
- Test plans
- Scheduler settings
- Device type mappings
- Health check settings

### `config/api-config.toml`

KernelCI API configuration:

- Server settings
- Database connection
- JWT authentication
- OpenWrt-specific settings

### `config/mongo-init.js`

MongoDB initialization:

- Creates collections
- Sets up indexes
- Optimizes queries

## API Reference

The KernelCI API uses a **Node-based data model** where all entities
(firmware builds, jobs, tests) are nodes with different `kind` values.

### Query Nodes

```bash
# Get all available jobs for a device type
GET /latest/nodes?kind=job&state=available&data.device_type=ath79-tplink-archer-c7-v2

# Get firmware nodes
GET /latest/nodes?kind=kbuild&data.target=ath79

# Get test results for a job
GET /latest/nodes?kind=test&parent={job_id}
```

### Create Nodes

```bash
# Create firmware node
POST /latest/nodes
{
  "kind": "kbuild",
  "name": "openwrt-ath79-generic-tplink_archer-c7-v2",
  "state": "available",
  "data": {
    "target": "ath79",
    "subtarget": "generic",
    "profile": "tplink_archer-c7-v2",
    "version": "24.10.0"
  }
}

# Create test result
POST /latest/nodes
{
  "kind": "test",
  "name": "test_firmware_version",
  "parent": "{job_id}",
  "state": "done",
  "result": "pass"
}
```

### Update Nodes

```bash
# Claim a job
PUT /latest/nodes/{job_id}
{
  "state": "running",
  "data": {
    "lab_name": "my-lab",
    "device_id": "device-01"
  }
}

# Complete a job
PUT /latest/nodes/{job_id}
{
  "state": "done",
  "result": "pass"
}
```

### Node States

| State | Description |
|-------|-------------|
| `available` | Ready to be processed (job ready for lab) |
| `running` | Currently being processed |
| `done` | Processing complete |

### Node Kinds

| Kind | Description |
|------|-------------|
| `kbuild` | Firmware build (OpenWrt image) |
| `job` | Test job container |
| `test` | Individual test result |

## Maintenance

### Backup

```bash
# Backup MongoDB
docker exec openwrt-kci-mongodb mongodump --out /backup
docker cp openwrt-kci-mongodb:/backup ./backup-$(date +%Y%m%d)

# Backup MinIO
docker run --rm -v openwrt-kci-minio:/data -v $(pwd):/backup \
    alpine tar czf /backup/minio-$(date +%Y%m%d).tar.gz /data
```

### Logs

```bash
# View all logs
docker compose logs -f

# View specific service
docker compose logs -f pipeline-health

# View last 100 lines
docker compose logs --tail=100 kernelci-api
```

### Updates

```bash
# Pull latest images
docker compose pull

# Restart with new images
docker compose up -d

# Rebuild pipeline services
docker compose build --no-cache
docker compose up -d
```

## Troubleshooting

### API not starting

Check MongoDB connection:

```bash
docker compose logs mongodb
docker exec -it openwrt-kci-mongodb mongosh --eval "db.adminCommand('ping')"
```

### Jobs not being scheduled

Check scheduler logs:

```bash
docker compose logs -f pipeline-scheduler
```

### Device health checks failing

Health checks run on the lab-side adapter, not centrally.
Check the adapter logs on your lab server:

```bash
docker logs labgrid-runner
```

### TLS certificate issues

Check Traefik logs:

```bash
docker compose logs -f traefik
```

Ensure your domain DNS is correctly configured.

## Development

### Local development without TLS

Add to `.env`:

```
SKIP_TLS=true
```

Access via `http://localhost:3000` (dashboard) and `http://localhost:8001` (API).

### Running tests

```bash
# Build and run tests
docker compose -f docker-compose.yml -f docker-compose.test.yml up --build
```

## License

This project is part of the OpenWrt testing infrastructure.
See the main repository for license information.
