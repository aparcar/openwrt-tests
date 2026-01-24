# OpenWrt Test Lab - Raspberry Pi Setup

Docker-based labgrid setup for Raspberry Pi with automatic container updates.

## Quick Start (Cloud-Init)

The easiest way - automatic setup on first boot:

```bash
# 1. Flash Raspberry Pi OS Lite (64-bit) to SD card
# Use Raspberry Pi Imager: https://www.raspberrypi.com/software/

# 2. Mount the boot partition and copy cloud-init files
cp cloud-init/user-data /media/$USER/bootfs/
cp cloud-init/meta-data /media/$USER/bootfs/

# 3. Edit user-data to add your SSH keys (search for "ADD YOUR SSH KEYS")
nano /media/$USER/bootfs/user-data

# 4. Unmount and boot the Pi
# First boot takes ~5-10 minutes to install Docker and pull images
```

## Quick Start (Manual)

```bash
# 1. Flash Raspberry Pi OS Lite and boot
# 2. SSH into the Pi and run:
curl -fsSL https://raw.githubusercontent.com/openwrt/openwrt-tests/main/coreos/raspberry-pi/setup.sh | sudo bash

# 3. Configure your devices
sudo nano /opt/labgrid/config/labgrid/exporter.yaml

# 4. Start services
sudo systemctl start labgrid
```

## Finding Serial Device Paths

```bash
# List all USB serial devices with their ID_PATH
labgrid-find-serial

# Example output:
# Device: /dev/ttyUSB0
# ID_PATH=platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.1:1.0
# ID_SERIAL=FTDI_FT232R_USB_UART_A50285BI
```

Use the `ID_PATH` value in your `exporter.yaml`.

## Configuration Files

All config files are in `/opt/labgrid/config/`:

```
/opt/labgrid/
├── docker-compose.yml
├── config/
│   ├── labgrid/
│   │   └── exporter.yaml    # Device definitions
│   ├── pdudaemon/
│   │   └── pdudaemon.conf   # PDU configuration
│   └── dnsmasq/
│       └── *.conf           # VLAN DHCP configs
└── tftp/                    # TFTP root for firmware
```

### Example Device Configuration

Edit `/opt/labgrid/config/labgrid/exporter.yaml`:

```yaml
openwrt-one:
  USBSerialPort:
    match:
      # Get this from: labgrid-find-serial
      ID_PATH: platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.1:1.0
    speed: 115200
  NetworkService:
    address: 192.168.101.1%vlan101
    username: root
  PDUDaemonPort:
    host: localhost:16421
    pdu: 192.168.128.2
    index: 1
  TFTPProvider:
    internal: /srv/tftp/openwrt-one/
    external: openwrt-one/
```

### PDU Configuration

Edit `/opt/labgrid/config/pdudaemon/pdudaemon.conf`:

```json
{
  "daemon": {
    "hostname": "0.0.0.0",
    "port": 16421,
    "logging_level": "INFO"
  },
  "pdus": {
    "192.168.128.2": {
      "driver": "ubus"
    }
  }
}
```

## VLAN Setup

```bash
# Create VLAN interface
sudo ip link add link eth0 name vlan101 type vlan id 101
sudo ip addr add 192.168.101.1/24 dev vlan101
sudo ip link set vlan101 up

# Make persistent (add to /etc/network/interfaces.d/vlans)
echo "auto vlan101
iface vlan101 inet static
    address 192.168.101.1/24
    vlan-raw-device eth0" | sudo tee /etc/network/interfaces.d/vlans
```

## Managing Services

```bash
# Start/stop all services
sudo systemctl start labgrid
sudo systemctl stop labgrid

# Or use docker compose directly
cd /opt/labgrid
docker compose up -d
docker compose down

# View logs
docker logs -f labgrid-exporter
docker logs -f labgrid-coordinator

# Restart a single service
docker compose restart labgrid-exporter
```

## Auto-Updates

Watchtower automatically updates containers daily at 4:00 AM:

```bash
# Check watchtower logs
docker logs watchtower

# Force update now
docker exec watchtower /watchtower --run-once
```

## Hardware Recommendations

### Raspberry Pi Models
- **Pi 5 (4GB+)**: Recommended - best USB and network performance
- **Pi 4 (4GB+)**: Works well
- **Pi 3**: Not recommended (limited USB bandwidth)

### USB Hub
Use a powered USB hub for multiple serial adapters:
- Plugable 7-Port USB 3.0 Hub
- Anker 7-Port USB 3.0 Hub

### Serial Adapters
- FTDI FT232R-based adapters (most reliable)
- CH340/CH341 adapters (budget option)

## Troubleshooting

### Serial devices not accessible

```bash
# Check device permissions
ls -la /dev/ttyUSB*

# Add user to dialout group
sudo usermod -aG dialout $USER

# Restart containers
docker compose restart labgrid-exporter
```

### Container won't start

```bash
# Check logs
docker logs labgrid-exporter

# Common issues:
# - Serial device doesn't exist: update devices in docker-compose.yml
# - Config error: check exporter.yaml syntax
```

### Network issues with VLANs

```bash
# Verify VLAN interface exists
ip -d link show vlan101

# Check routing
ip route

# Test connectivity
ping -I vlan101 192.168.101.100
```
