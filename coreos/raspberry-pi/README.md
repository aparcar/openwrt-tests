# OpenWrt Test Lab - Raspberry Pi

Fedora CoreOS on Raspberry Pi with safe, atomic auto-updates.

## Quick Start

```bash
# 1. Create config (optional but recommended)
cp ../lab-config.yaml.example ../lab-config.yaml
nano ../lab-config.yaml   # Add SSH keys

# 2. Generate ignition
../scripts/build-ignition.sh ../lab-config.yaml -o config.ign

# 3. Build image (no root needed, uses podman)
./build-image.sh config.ign -o labnode.img

# 4. Flash (only step requiring root)
sudo dd if=labnode.img of=/dev/sdX bs=4M status=progress conv=fsync
```

Requires: **podman** (or docker), curl, unzip, xz

## What build-image.sh Does

1. Downloads Fedora CoreOS (aarch64, stable)
2. Downloads Raspberry Pi UEFI firmware
3. Embeds ignition config into image
4. Outputs ready-to-flash `.img` file

All image manipulation runs inside a container - no root on host.

## Without Config (Minimal Install)

```bash
./build-image.sh -o minimal.img
sudo dd if=minimal.img of=/dev/sdX bs=4M status=progress
```

Default user is `core` - you'll need console access to add SSH keys.

## First Boot - UEFI Setup

1. Connect monitor + keyboard
2. Power on, press **Esc** for UEFI
3. **Device Manager → Raspberry Pi Configuration → Advanced**
4. **Limit RAM to 3GB → Disabled**
5. **F10** save, **Esc** exit

This is a one-time setup.

## After First Boot

Ignition only runs once. To change config later:

```bash
# SSH into the Pi
ssh labgrid@<ip>

# Edit configs directly
sudo nano /etc/labgrid/exporter.yaml
sudo systemctl restart labgrid-exporter
```

## Auto-Updates

| Component | Method | Schedule | Rollback |
|-----------|--------|----------|----------|
| **Fedora CoreOS** | Zincati + rpm-ostree | Sundays 3am | Automatic |
| **Containers** | Podman auto-update | Daily 4am | Manual |

```bash
# Check OS update status
rpm-ostree status

# Check container updates
sudo podman auto-update --dry-run
```

### Why Updates Are Safe

- **A/B partitions**: Updates install to inactive partition
- **Auto-rollback**: Failed boot (3x) reverts automatically
- **Immutable OS**: Can't accidentally break with apt/dnf

## Finding Serial Devices

After boot:

```bash
ls /dev/ttyUSB*
udevadm info /dev/ttyUSB0 | grep ID_PATH

# Typical Pi 4/5 path:
# platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.1:1.0
```

## Minimal lab-config.yaml

```yaml
lab:
  hostname: labgrid-pi

ssh_keys:
  - ssh-ed25519 AAAA... you@host

# Add devices after first boot
devices: []
```

## Troubleshooting

### Won't boot past UEFI
- Disable 3GB RAM limit in UEFI settings

### Can't SSH
- Check ignition was embedded: `./build-image.sh config.ign`
- Default user is `core` without config

### Container issues
```bash
sudo journalctl -u labgrid-exporter -f
sudo podman ps
```
