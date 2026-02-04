# KernelCI Labgrid Adapter Setup Context

## Last Updated: 2026-02-04

## Infrastructure Overview

### Servers
1. **openwrt-kci.aparcar.org** (KernelCI API server)
   - SSH: `ssh root@openwrt-kci.aparcar.org`
   - Runs Docker containers for KernelCI API, MongoDB, Redis, MinIO, etc.
   - Main compose at `/opt/openwrt-pipeline/docker-compose.yml`

2. **labgrid-aparcar** (Labgrid coordinator + adapter)
   - SSH: `ssh labgrid-aparcar` (logs in as labgrid-dev user)
   - Runs labgrid-coordinator and labgrid-exporter as systemd services
   - Adapter deployed at `~/labgrid-adapter`

### Running Services

**On openwrt-kci.aparcar.org:**
```
openwrt-kci-api       - KernelCI API (FastAPI)
openwrt-kci-mongodb   - MongoDB database
openwrt-kci-redis     - Redis
openwrt-kci-minio     - S3-compatible storage
openwrt-kci-scheduler - Test scheduler
openwrt-kci-trigger   - Firmware trigger
openwrt-kci-bridge    - KCIDB bridge
```

**On labgrid-aparcar:**
```
labgrid-coordinator.service - Labgrid coordinator (port 20408)
labgrid-exporter.service    - Labgrid exporter
labgrid-adapter             - KernelCI adapter (running as background process)
```

## Labgrid Adapter Status

### Current State: RUNNING
- Process: `python -c 'from labgrid_kci_adapter.service import main; ...'`
- Log file: `~/adapter.log`
- Working directory: `~/labgrid-adapter`

### Configuration (`~/labgrid-adapter/.env`)
```
LAB_NAME=labgrid-aparcar
KCI_API_URL=https://api.openwrt-kci.aparcar.org
KCI_API_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2OTgwYmQ4Mzg2MDcyNTY3OGE4Y2U1YjAiLCJhdWQiOlsiZmFzdGFwaS11c2VyczphdXRoIl0sImV4cCI6MjA4NTQwNDgxMH0.S5hDyVz0E2SIELCZTi8n4CwTpRr_8Sqjn85FmlVHSeQ
LG_COORDINATOR=localhost:20408
POLL_INTERVAL=30
MAX_CONCURRENT_JOBS=3
HEALTH_CHECK_ENABLED=true
HEALTH_CHECK_INTERVAL=86400
SUPPORTED_TEST_TYPES=firmware
TARGETS_DIR=/home/labgrid-dev/labgrid-adapter/targets
TESTS_DIR=/home/labgrid-dev/labgrid-adapter/tests-openwrt
FIRMWARE_CACHE=/home/labgrid-dev/labgrid-adapter/cache
```

### Discovered Devices (6 total)
1. bananapi_bpi-r4
2. bananapi_bpi-r4-lite
3. genexis_pulse-ex400
4. openwrt_one
5. rpi-4
6. tplink_tl-wdr3600-v1

### Place Naming Convention
- Places are named: `labgrid-aparcar-{device_type}`
- LAB_NAME must be `labgrid-aparcar` (full prefix, not just `aparcar`)

## Code Changes Made

### Fixed place name construction
Files modified (need to be committed):
- `kernelci/labgrid-adapter/labgrid_kci_adapter/health_check.py`
- `kernelci/labgrid-adapter/labgrid_kci_adapter/service.py`

Change: Removed redundant `labgrid-` prefix since LAB_NAME already includes it.
```python
# Before: place_name = f"labgrid-{settings.lab_name}-{device_name}"
# After:  place_name = f"{settings.lab_name}-{device_name}"
```

## Known Issues

### 1. Admin User Auth Problem (DEFERRED)
- Created admin user in MongoDB (ID: `69828a0526ff6640ab0f248b`)
- Token verification works in Python but fails via HTTP API
- **Workaround:** Using pipeline token (user ID: `6980bd83860725678a8ce5b0` - doesn't exist in DB but token works)
- Root cause unclear - possibly related to fastapi-users + fastapi-versioning interaction

### 2. YAML Template Warnings
Target YAML files use labgrid's `!template` tag which requires special YAML loader.
These are warnings only, not blocking - devices still discovered from coordinator.

### 3. Pytest Exit Code 3
Tests complete with exit code 3 (no tests collected). The test execution flow works but actual tests may need configuration.

## Useful Commands

### Check adapter status
```bash
ssh labgrid-aparcar "pgrep -fa labgrid_kci_adapter"
ssh labgrid-aparcar "tail -50 ~/adapter.log"
```

### Restart adapter
```bash
ssh labgrid-aparcar "pkill -f labgrid_kci_adapter; cd ~/labgrid-adapter && source .venv/bin/activate && export \$(grep -v '^#' .env | xargs) && nohup python -c '
import asyncio
import logging
import sys
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from labgrid_kci_adapter.service import main
asyncio.run(main())
' >> ~/adapter.log 2>&1 &"
```

### Check labgrid places
```bash
ssh labgrid-aparcar "labgrid-client places"
```

### Check API status
```bash
curl -s https://api.openwrt-kci.aparcar.org/latest/
ssh root@openwrt-kci.aparcar.org "docker logs openwrt-kci-api 2>&1 | tail -30"
```

### Test API with token
```bash
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2OTgwYmQ4Mzg2MDcyNTY3OGE4Y2U1YjAiLCJhdWQiOlsiZmFzdGFwaS11c2VyczphdXRoIl0sImV4cCI6MjA4NTQwNDgxMH0.S5hDyVz0E2SIELCZTi8n4CwTpRr_8Sqjn85FmlVHSeQ"
curl -s -H "Authorization: Bearer $TOKEN" https://api.openwrt-kci.aparcar.org/latest/nodes?limit=5
```

### Sync adapter code changes
```bash
rsync -av --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.egg-info' kernelci/labgrid-adapter/ labgrid-aparcar:~/labgrid-adapter/
```

## API Configuration

### Secret Keys (IMPORTANT - they differ!)
- **openwrt-pipeline/.env:** `KCI_SECRET_KEY=ae914b257bee501de4af4e6c7c8a76bd4a99c7d9ecf2aed0f43f3f8c4f37041d`
- **kernelci/.env:** `KCI_SECRET_KEY=59f2184b5a24d282856eb5accd15278f02f0fe2d11b66a98357d0177f83ba59e` (NOT USED)
- The API container uses the openwrt-pipeline secret (compose runs from /opt/openwrt-pipeline)

### MongoDB
- Connection: `mongodb://admin:openwrt-mongo-32a6c8216d106e2c@mongodb:27017`
- Database: `openwrt_kernelci`

## Next Steps (TODO)
1. Set up systemd service for adapter (needs sudo access on labgrid-aparcar)
2. Debug admin user authentication issue
3. Configure actual test execution (fix pytest exit code 3)
4. Commit code changes to repository
