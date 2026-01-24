# OpenWrt KernelCI - Self-Hosted Testing Infrastructure

This directory contains the Docker Compose stack for running a self-hosted
KernelCI instance tailored for OpenWrt firmware testing.

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
│    (React)      │  │   (FastAPI)     │  │   (S3 Storage)  │
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
- **Buildbot** - Webhook integration

### Test Scheduler (`pipeline-scheduler`)

Assigns test jobs to available labs based on:

- Device compatibility (target/subtarget/profile)
- Device features (wifi, wan_port, etc.)
- Job priority
- Lab availability

### Health Scheduler (`pipeline-health`)

Monitors device health:

- Daily health checks on all devices
- Automatic device disable after failures
- GitHub issue creation for persistent failures
- Auto-close issues when devices recover

### Results Collector (`pipeline-results`)

Aggregates test results:

- Collects results from labs
- Stores console logs in MinIO
- Updates job/firmware status
- Triggers notifications

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

## Lab Integration

Labs connect using the **pull-mode** architecture:

1. Lab runs the `labgrid-adapter` service
2. Adapter polls API for pending jobs
3. Jobs are executed using labgrid
4. Results are submitted back to API

See `labgrid-adapter/` for the lab-side component.

## API Endpoints

### Firmware

```
POST   /api/v1/firmware/upload    - Upload custom firmware
GET    /api/v1/firmware           - List firmware
GET    /api/v1/firmware/{id}      - Get firmware details
```

### Jobs

```
GET    /api/v1/jobs               - List jobs
GET    /api/v1/jobs/pending       - Get pending jobs (for labs)
POST   /api/v1/jobs/{id}/start    - Mark job as started
POST   /api/v1/jobs/{id}/complete - Submit job results
```

### Devices

```
GET    /api/v1/devices            - List devices
GET    /api/v1/devices/{id}       - Get device status
POST   /api/v1/devices/{id}/health-check - Trigger health check
```

### Labs

```
POST   /api/v1/labs/register      - Register a lab
GET    /api/v1/labs               - List labs
POST   /api/v1/labs/{id}/heartbeat - Lab heartbeat
```

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

### Health checks failing

Check health service logs:

```bash
docker compose logs -f pipeline-health
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
