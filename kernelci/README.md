# OpenWrt KernelCI

Self-hosted [KernelCI](https://kernelci.org/) instance for automated OpenWrt
firmware testing on real hardware. Includes the full KernelCI dashboard for
browsing test results.

## Architecture

```
  downloads.openwrt.org
          │
          ▼
  ┌───────────────┐        ┌───────────────┐        ┌───────────────┐
  │   Firmware     │───────▶│  KernelCI API │◀───────│  Labgrid      │
  │   Trigger      │ create │  (Maestro)    │  poll  │  Runner       │
  └───────────────┘ kbuild └───────┬───────┘        └───────┬───────┘
                     nodes         │                        │
                            ┌──────┴──────┐                 │
                            ▼             ▼                 │
                    ┌─────────────┐ ┌───────────┐          │
                    │    Test     │ │   KCIDB   │          │
                    │  Scheduler  │ │   Bridge  │          │
                    └─────────────┘ └─────┬─────┘          │
                     create job           │ submit          │
                     nodes                ▼                 │
                                  ┌───────────────┐        │
                                  │  KCIDB REST   │        │
                                  │  (Rust)       │        │
                                  └───────┬───────┘        │
                                          │ spool          │
                                          ▼                │
                                  ┌───────────────┐        │
                                  │   Ingester    │        │
                                  │  (Django)     │        │
                                  └───────┬───────┘        │
                                          │                │
                                          ▼                │
                                  ┌───────────────┐        │
                                  │  PostgreSQL   │        │
                                  │  (KCIDB)      │        │
                                  └───────┬───────┘        │
                                          │                │
                                          ▼                │
                                  ┌───────────────┐        │
                                  │   Dashboard   │        │
                                  │  (React+Django)│       │
                                  └───────────────┘        │
                                                    (in each lab)
```

**Routing:**
- `https://DOMAIN` — Dashboard (tree view, test results)
- `https://api.DOMAIN` — Maestro API (Swagger UI at `/docs`)
- `https://storage.DOMAIN` — MinIO console (log storage)

| Component | Description | Reusable? |
|-----------|-------------|-----------|
| `openwrt-scheduler/` | Firmware discovery + test scheduling + KCIDB bridge | OpenWrt-specific |
| `labgrid-runner/` | Generic labgrid-to-KernelCI adapter | **Yes** — any project |
| `kcidb-ng/` | KCIDB result storage (cloned at deploy time) | Upstream KernelCI |
| `dashboard/` | KernelCI dashboard (cloned at deploy time) | Upstream KernelCI |

## Prerequisites

- Docker Engine 24+ with Compose v2
- A domain name with DNS A records for `DOMAIN`, `api.DOMAIN`, `storage.DOMAIN`
- Ports 80 and 443 open (for Let's Encrypt HTTP challenge + HTTPS)
- 4 GB RAM, 20 GB disk

## Deployment

### 1. Clone repositories

```bash
git clone https://github.com/openwrt/openwrt.git
cd openwrt/tests/kernelci

# Clone upstream KernelCI components
git clone https://github.com/kernelci/kcidb-ng
git clone https://github.com/kernelci/dashboard
```

### 2. Configure environment

```bash
cp .env.example .env
```

Generate secrets and paste into `.env`:

```bash
openssl rand -hex 16   # → MONGO_PASSWORD
openssl rand -hex 32   # → KCI_SECRET_KEY
openssl rand -hex 16   # → KCIDB_PG_PASSWORD
openssl rand -hex 32   # → KCIDB_JWT_SECRET
openssl rand -hex 16   # → MINIO_SECRET_KEY
```

Set `DOMAIN` and `ACME_EMAIL` to your values. Leave `KCI_API_TOKEN` empty
for now.

### 3. Create dashboard secrets

The dashboard backend reads the PostgreSQL password from a file:

```bash
echo "YOUR_KCIDB_PG_PASSWORD" > config/dashboard-secrets/postgres_password_secret
```

Use the same value you set for `KCIDB_PG_PASSWORD` in `.env`.

### 4. Build the KernelCI API image

The upstream container image (`ghcr.io/kernelci/kernelci-api:latest`) may fail
to pull. Build from source as a reliable fallback:

```bash
git clone https://github.com/kernelci/kernelci-api.git /tmp/kernelci-api
docker build -t ghcr.io/kernelci/kernelci-api:latest /tmp/kernelci-api
```

### 5. Start core services

Start the infrastructure first (without pipeline services):

```bash
docker compose up -d --build \
  mongodb maestro-redis kernelci-api traefik \
  kcidb-db kcidb-dbinit kcidb-rest kcidb-ingester \
  minio minio-init \
  dashboard-db dashboard-redis dashboard-backend dashboard dashboard-proxy
```

Wait for services to become healthy:

```bash
docker compose logs -f kernelci-api    # "Application startup complete"
docker compose logs -f kcidb-dbinit    # "Database initialized."
```

Verify the dashboard loads:

```bash
curl -I https://YOUR_DOMAIN
# Should return 200
```

### 6. Create an API user and token

```bash
# Create a user
curl -X POST https://api.YOUR_DOMAIN/latest/user \
  -H "Content-Type: application/json" \
  -d '{"username": "pipeline", "password": "pipeline-secret", "is_superuser": true}'

# Get a token
curl -X POST https://api.YOUR_DOMAIN/latest/user/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=pipeline&password=pipeline-secret"
```

Copy the `access_token` from the response into `.env` as `KCI_API_TOKEN`.

### 7. Start the pipeline

```bash
docker compose up -d --build
```

This starts the firmware trigger, test scheduler, and KCIDB bridge. Check logs:

```bash
docker compose logs -f pipeline-trigger   # Firmware scanning
docker compose logs -f kcidb-bridge       # Result submission to dashboard
```

After a few minutes, firmware builds should appear on the dashboard at
`https://YOUR_DOMAIN`.

### 8. Connect a lab

Labs run the `labgrid-runner` service, which polls the API for available jobs,
runs tests via pytest + labgrid, and submits results back. See
[labgrid-runner/README.md](labgrid-runner/README.md) for setup instructions.

## Customizing for your project

### Adding firmware targets

Edit `config/pipeline.yaml` under `firmware_sources.official.targets`:

```yaml
firmware_sources:
  official:
    targets:
      - x86/64
      - ath79/generic
      - ramips/mt7621
      - mediatek/filogic
```

### Adding device types

Edit `config/pipeline.yaml` under `device_types`:

```yaml
device_types:
  my-router:
    target: ath79
    subtarget: generic
    profile: tplink_archer-c7-v2
    features:
      - wifi
      - wan_port
    capabilities:
      - serial_console
```

The scheduler matches kbuild nodes to device types by `target/subtarget/profile`
and creates job nodes with test plans based on the device's features.

### Test plans

Test plans in `config/pipeline.yaml` list the pytest functions to run and
required device features:

```yaml
test_plans:
  base:
    tests: [test_shell, test_ssh, test_firmware_version, ...]
    required_features: []
  wifi:
    tests: [test_wifi_scan, test_wifi_wpa2, test_wifi_wpa3]
    required_features: [wifi]
```

## Configuration reference

| File | Purpose |
|------|---------|
| `.env` | Secrets and domain config (not committed) |
| `config/api-config.toml` | KernelCI Maestro API settings (port, DB, JWT) |
| `config/pipeline.yaml` | Firmware sources, targets, device types, test plans |
| `config/dbinit.sh` | KCIDB PostgreSQL schema initialization |
| `config/dashboard-secrets/` | Dashboard backend secrets (postgres password file) |
| `docker-compose.yml` | All service definitions |

## API quick reference

The Maestro API uses a node-based data model at `https://api.YOUR_DOMAIN`.

| Kind | Description | Created by |
|------|-------------|------------|
| `kbuild` | Firmware build | Trigger |
| `job` | Test job | Scheduler |
| `test` | Test result | Lab runner |

```bash
# List firmware builds
curl https://api.YOUR_DOMAIN/latest/nodes?kind=kbuild

# List available jobs
curl https://api.YOUR_DOMAIN/latest/nodes?kind=job&state=available

# Swagger UI
open https://api.YOUR_DOMAIN/docs
```

## Troubleshooting

### API image fails to pull

Build from source (see step 4 above).

### No kbuild nodes created

Check trigger logs: `docker compose logs pipeline-trigger`

Common cause: x86/64 uses `combined`/`combined-efi` images, not `sysupgrade`.
Make sure the firmware source code handles your target's image naming.

### Dashboard shows no data

Check the KCIDB bridge logs: `docker compose logs kcidb-bridge`

The bridge polls Maestro for completed nodes and submits them to KCIDB. Data
only appears on the dashboard after the ingester processes submissions from the
spool directory.

Check ingester: `docker compose logs kcidb-ingester`

### HTTPS not working

- Verify DNS A records for `DOMAIN`, `api.DOMAIN`, `storage.DOMAIN`
- Check Traefik logs: `docker compose logs traefik`
- After changing `DOMAIN` in `.env`, recreate containers:
  ```bash
  docker compose up -d --force-recreate
  ```

### Database initialization fails

Check dbinit logs: `docker compose logs kcidb-dbinit`

The dbinit container runs once and exits. If it fails, fix the issue and run:
```bash
docker compose up kcidb-dbinit
```
