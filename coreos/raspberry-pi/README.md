# OpenWrt Test Lab - Raspberry Pi

Fedora CoreOS on Raspberry Pi 4/5 with auto-updating containers.

## Quick Start

```bash
# 1. Create your lab config
cp ../lab-config.yaml.example ../lab-config.yaml
nano ../lab-config.yaml  # Add SSH keys, devices, PDUs

# 2. Flash SD card
sudo ./flash-coreos.sh /dev/sdX

# 3. Boot Pi, do one-time UEFI setup, done!
```

## Requirements

- Raspberry Pi 4 (4GB+) or Pi 5
- SD card 32GB+ (or USB/NVMe storage)
- Monitor + keyboard (first boot only, for UEFI setup)

## Installation

### 1. Configure Your Lab

```bash
cd coreos/
cp lab-config.yaml.example lab-config.yaml
nano lab-config.yaml
```

Minimum config:
```yaml
lab:
  name: my-lab
  hostname: labgrid-pi

ssh_keys:
  - ssh-ed25519 AAAA... your-key@hostname

devices:
  - name: my-router
    serial:
      id_path: platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1:1.0
    network:
      vlan: 101
    power:
      pdu: 192.168.128.2
      outlet: 1
```

### 2. Flash SD Card

```bash
# Find your SD card
lsblk

# Flash (downloads CoreOS + UEFI firmware)
sudo ./raspberry-pi/flash-coreos.sh /dev/sdX
```

### 3. First Boot - UEFI Setup (One Time)

1. Insert SD card, connect monitor + keyboard
2. Power on, press **Esc** to enter UEFI
3. Go to: **Device Manager → Raspberry Pi Configuration → Advanced**
4. Set **Limit RAM to 3GB → Disabled**
5. Press **F10** to save, **Esc** to exit
6. Pi boots Fedora CoreOS

### 4. Verify

```bash
ssh labgrid@<pi-ip>
sudo podman ps
```

## Finding Serial Paths

After boot, find your USB serial devices:

```bash
# List devices
ls /dev/ttyUSB*

# Get ID_PATH
udevadm info /dev/ttyUSB0 | grep ID_PATH=

# Typical Pi 4 path:
# platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.1:1.0
```

## Auto-Updates

| Component | Schedule | Method |
|-----------|----------|--------|
| Fedora CoreOS | Sundays 3am | Zincati (rpm-ostree) |
| Containers | Daily 4am | Podman auto-update |

```bash
# Check OS updates
rpm-ostree status

# Check container updates
sudo podman auto-update --dry-run
```

## Configuration

Configs are in `/etc/`:

```
/etc/labgrid/exporter.yaml     # Devices
/etc/pdudaemon/pdudaemon.conf  # PDUs
/etc/dnsmasq.d/*.conf          # DHCP
/srv/tftp/                     # Firmware
```

Edit and restart:
```bash
sudo nano /etc/labgrid/exporter.yaml
sudo systemctl restart labgrid-exporter
```

## Troubleshooting

### Won't boot past UEFI
- Disable 3GB RAM limit in UEFI settings
- `nomodeset` is added automatically by flash script

### No serial devices
```bash
lsusb                              # Check USB
ls -la /dev/ttyUSB*                # Check devices
sudo podman exec labgrid-exporter ls /dev/
```

### Container errors
```bash
sudo journalctl -u labgrid-exporter -f
```

## Alternative: Raspberry Pi OS + Docker

If you prefer Raspberry Pi OS:

```bash
# Use cloud-init (automatic)
cp cloud-init/user-data /media/$USER/bootfs/
cp cloud-init/meta-data /media/$USER/bootfs/
# Edit user-data, add SSH keys, boot

# Or manual setup
sudo ./setup.sh
```

## Hardware

**Recommended:**
- Raspberry Pi 5 (8GB) or Pi 4 (4GB+)
- Powered USB 3.0 hub
- FTDI-based serial adapters

## References

- [Fedora CoreOS on RPi4 - Docs](https://docs.fedoraproject.org/es_419/fedora-coreos/provisioning-raspberry-pi4/)
- [RPi4 UEFI Firmware](https://github.com/pftf/RPi4)
- [RPi Forum Guide](https://forums.raspberrypi.com/viewtopic.php?t=381870)
