# Aparcar Lab Deployment

Configuration files for deploying the labgrid-kci-adapter on the aparcar lab host.

## Prerequisites

- labgrid-coordinator running on localhost:20408
- Python 3.11+ with pipx
- Network access to api.openwrt-kci.aparcar.org

## Generate API Token

Generate a JWT token for this lab using the KCI_SECRET_KEY from the API server:

```bash
# On the machine running the KernelCI API, get the secret key
# Then generate a token:
cd /path/to/openwrt-tests
python scripts/generate-lab-token.py aparcar "YOUR_KCI_SECRET_KEY" --expires-days 365
```

Copy the generated token to the `.env` file.

## Installation

1. **Copy files to lab host:**

```bash
scp .env labgrid-aparcar:/home/labgrid-dev/labgrid-kci-adapter/
scp labgrid-kci-adapter.service labgrid-aparcar:/tmp/
```

2. **Install the adapter:**

```bash
ssh labgrid-aparcar

# Install via pipx (as labgrid-dev user)
pipx install git+https://github.com/aparcar/openwrt-tests.git#subdirectory=kernelci/labgrid-adapter

# Or install from local checkout
cd ~/labgrid-kci-adapter
pipx install .
```

3. **Install systemd service:**

```bash
sudo cp /tmp/labgrid-kci-adapter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable labgrid-kci-adapter
sudo systemctl start labgrid-kci-adapter
```

4. **Verify:**

```bash
sudo systemctl status labgrid-kci-adapter
sudo journalctl -u labgrid-kci-adapter -f
```

## Configuration

Edit `/home/labgrid-dev/labgrid-kci-adapter/.env` to configure:

| Variable | Description | Default |
|----------|-------------|---------|
| `LAB_NAME` | Lab identifier | `aparcar` |
| `KCI_API_URL` | KernelCI API URL | `https://api.openwrt-kci.aparcar.org` |
| `KCI_API_TOKEN` | JWT auth token | (required) |
| `LG_COORDINATOR` | Labgrid coordinator | `localhost:20408` |
| `TESTS_REPO_URL` | Git repo with tests | `https://github.com/aparcar/openwrt-tests` |
| `REQUIRE_TARGET_FILES` | Validate targets | `false` |
| `MAX_CONCURRENT_JOBS` | Parallel jobs | `3` |

## Device Discovery

The adapter automatically discovers devices from the labgrid coordinator. Devices need the `device` tag set:

```bash
labgrid-client set-tags labgrid-aparcar-openwrt_one device=openwrt_one
```

Check discovered devices:

```bash
labgrid-client -v places
```

## Troubleshooting

**Check logs:**
```bash
sudo journalctl -u labgrid-kci-adapter -f --no-pager
```

**Test discovery manually:**
```bash
cd ~/labgrid-kci-adapter
./venv/bin/python -c "
import asyncio
from labgrid_kci_adapter.labgrid_client import LabgridClient

async def test():
    client = LabgridClient('localhost:20408')
    places = await client.get_places()
    for name, place in places.items():
        print(f'{name}: device_type={place.device_type}')

asyncio.run(test())
"
```

**Test API connection:**
```bash
curl -H "Authorization: Bearer $KCI_API_TOKEN" \
  https://api.openwrt-kci.aparcar.org/latest/nodes?limit=1
```
