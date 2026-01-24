# OpenWrt Test Lab - Containerized Infrastructure

Self-contained, auto-updating lab node setup for remote OpenWrt test labs with containerized services.

## Quick Start

```bash
# 1. Create your lab config
cp lab-config.yaml.example lab-config.yaml
nano lab-config.yaml   # Add SSH keys, devices

# 2. Generate ignition file
./scripts/build-ignition.sh lab-config.yaml -o config.ign

# 3. Flash SD card (Raspberry Pi) - uses podman container
sudo ./raspberry-pi/flash-sd.sh /dev/sdX config.ign

# 4. Boot Pi, configure UEFI once, done!
```

Only requires **podman** (or docker) - no other tools to install.

See [raspberry-pi/README.md](raspberry-pi/README.md) for details.

## x86 Servers

```bash
# Use coreos-installer directly (or via container)
podman run --rm --privileged -v /dev:/dev -v .:/data:ro \
    quay.io/coreos/coreos-installer:release \
    install /dev/sdX --ignition-file /data/config.ign
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Fedora CoreOS Host                        │
│                                                              │
│  ┌──────────────────┐  ┌──────────────────┐                 │
│  │ labgrid-coord    │  │ labgrid-exporter │                 │
│  │ (container)      │◄─┤ (container)      │                 │
│  │ :20408           │  │ host network     │                 │
│  └──────────────────┘  └────────┬─────────┘                 │
│                                 │                            │
│  ┌──────────────────┐  ┌───────┴──────────┐                 │
│  │ pdudaemon        │  │ dnsmasq          │                 │
│  │ (container)      │  │ (container)      │                 │
│  │ :16421           │  │ DHCP/TFTP        │                 │
│  └────────┬─────────┘  └───────┬──────────┘                 │
│           │                    │                             │
│  ┌────────┴────────────────────┴──────────┐                 │
│  │           Host Network / VLANs          │                 │
│  └────────────────────┬───────────────────┘                 │
└───────────────────────┼─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
   ┌────┴────┐    ┌────┴────┐    ┌────┴────┐
   │ Device 1│    │ Device 2│    │ Device N│
   │ (VLAN)  │    │ (VLAN)  │    │ (VLAN)  │
   └─────────┘    └─────────┘    └─────────┘
```

## Configuration

### Lab Configuration File

Create `lab-config.yaml` from the example:

```yaml
# Lab identification
lab:
  name: my-lab
  hostname: labgrid-mylab

# SSH access - add your public keys
ssh_keys:
  - ssh-ed25519 AAAAC3Nz... admin@example.com

# Network VLANs for device isolation
network:
  interface: eth0
  vlans:
    - id: 101
      address: 192.168.101.1/24
      dhcp_start: 192.168.101.100
      dhcp_end: 192.168.101.200

# Power distribution units
pdus:
  - address: 192.168.128.2
    driver: ubus

# Test devices
devices:
  - name: openwrt-one
    serial:
      id_path: pci-0000:00:14.0-usb-0:1:1.0  # from: udevadm info /dev/ttyUSB0
      speed: 115200
    network:
      vlan: 101
    power:
      pdu: 192.168.128.2
      outlet: 1
```

### Finding Serial Port ID_PATH

```bash
# List USB serial devices
ls /dev/ttyUSB* /dev/ttyACM*

# Get ID_PATH for a device
udevadm info /dev/ttyUSB0 | grep ID_PATH
```

## Directory Structure

```
coreos/
├── lab-config.yaml.example  # Template - copy and customize
├── containers/              # Container definitions
│   ├── Containerfile.labgrid
│   ├── Containerfile.pdudaemon
│   ├── Containerfile.dnsmasq
│   └── entrypoint-labgrid.sh
├── scripts/
│   ├── build-ignition.sh    # Main build script
│   ├── generate-butane.py   # Config → Butane converter
│   └── build-containers.sh  # Container image builder
├── ignition/                # Advanced: raw Butane configs
└── quadlet/                 # Advanced: container unit files
```

## Container Images

Pre-built images from GitHub Container Registry:

| Image | Description |
|-------|-------------|
| `ghcr.io/openwrt/openwrt-tests/labgrid` | Coordinator and exporter |
| `ghcr.io/openwrt/openwrt-tests/pdudaemon` | Power control daemon |
| `ghcr.io/openwrt/openwrt-tests/dnsmasq` | DHCP/TFTP server |

Images are automatically rebuilt weekly and on changes.

### Building Locally

```bash
./scripts/build-containers.sh           # Build all
./scripts/build-containers.sh --push    # Build and push
```

## Auto-Updates

### OS Updates

Fedora CoreOS updates automatically via Zincati:
- **Default schedule**: Sundays 03:00 UTC
- **Automatic rollback** on boot failure

```bash
rpm-ostree status          # Check current/pending updates
systemctl status zincati   # Update service status
```

### Container Updates

Containers update daily via Podman auto-update:
- **Default schedule**: Daily 04:00 UTC
- Pulls new `:latest` images automatically

```bash
sudo podman auto-update --dry-run  # Check for updates
sudo podman auto-update            # Update now
```

## Post-Installation

### Verify Services

```bash
# Check containers
sudo podman ps

# Check services
systemctl status labgrid-coordinator labgrid-exporter pdudaemon dnsmasq

# View logs
journalctl -u labgrid-exporter -f
```

### Configure VLANs (if not using ignition network config)

```bash
# Create VLAN interface
sudo nmcli con add type vlan con-name vlan101 dev eth0 id 101 \
    ipv4.addresses 192.168.101.1/24 ipv4.method manual
sudo nmcli con up vlan101
```

## Troubleshooting

### Container Logs

```bash
sudo podman logs labgrid-coordinator
sudo podman logs labgrid-exporter
sudo podman logs pdudaemon
```

### Restart Services

```bash
sudo systemctl restart labgrid-exporter
sudo systemctl daemon-reload  # After config changes
```

### Serial Devices Not Found

```bash
# Check devices exist
ls -la /dev/ttyUSB* /dev/ttyACM*

# Check container has access
sudo podman exec labgrid-exporter ls /dev/ttyUSB0
```

## Hardware Requirements

- **CPU**: x86_64 or aarch64
- **RAM**: 2GB minimum
- **Storage**: 16GB minimum
- **Network**: Gigabit Ethernet
- **USB**: Ports for serial consoles

### Tested Platforms

- Raspberry Pi 5
- Intel NUC
- Generic x86_64 servers

## Advanced: Manual Butane Configuration

For complex setups, you can edit the Butane files directly:

```bash
# Edit the standalone Butane file
vim ignition/labnode-standalone.bu

# Generate Ignition
./scripts/generate-ignition.sh ignition/labnode-standalone.bu
```

## Contributing

1. Edit config template: `lab-config.yaml.example`
2. Edit containers: `containers/Containerfile.*`
3. Edit generator: `scripts/generate-butane.py`
4. Container images auto-build on push to main
