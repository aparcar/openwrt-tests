# OpenWrt Test Lab - Raspberry Pi

Fedora CoreOS on Raspberry Pi with safe, atomic auto-updates.

## Quick Start

```bash
# 1. Create config (optional but recommended)
cp ../lab-config.yaml.example ../lab-config.yaml
nano ../lab-config.yaml   # Add SSH keys

# 2. Generate ignition
../scripts/build-ignition.sh ../lab-config.yaml -o config.ign

# 3. Flash SD card (uses podman, no install needed)
sudo ./flash-sd.sh /dev/sdX config.ign
```

Only requires **podman** (or docker) - coreos-installer runs in a container.

## Without Config (Minimal Install)

```bash
# Just flash, add SSH keys later
sudo ./flash-sd.sh /dev/sdX
```

## Manual Method (most control)

### 1. Download and flash CoreOS image

```bash
# Download latest stable aarch64 image
curl -LO https://builds.coreos.fedoraproject.org/prod/streams/stable/builds/.../fedora-coreos-...-metal.aarch64.raw.xz

# Flash to SD card
xzcat fedora-coreos-*.raw.xz | sudo dd of=/dev/sdX bs=4M status=progress
```

### 2. Add ignition config

```bash
# Mount boot partition (partition 2 on CoreOS)
sudo mount /dev/sdX2 /mnt

# Copy your ignition file
sudo mkdir -p /mnt/ignition
sudo cp config.ign /mnt/ignition/config.ign

sudo umount /mnt
```

### 3. Add Raspberry Pi UEFI firmware

```bash
# Mount EFI partition (partition 1)
sudo mount /dev/sdX1 /mnt

# Download and extract UEFI firmware
curl -LO https://github.com/pftf/RPi4/releases/download/v1.39/RPi4_UEFI_Firmware_v1.39.zip
sudo unzip RPi4_UEFI_Firmware_v1.39.zip -d /mnt/

sudo umount /mnt
```

### 4. First boot UEFI setup

1. Connect monitor + keyboard
2. Power on, press **Esc** for UEFI
3. **Device Manager → Raspberry Pi Configuration → Advanced**
4. **Limit RAM to 3GB → Disabled**
5. **F10** save, **Esc** exit

## Applying Config Changes Later

Ignition only runs on **first boot**. To change config later:

```bash
# SSH into the Pi
ssh labgrid@<ip>

# Edit configs directly
sudo nano /etc/labgrid/exporter.yaml
sudo systemctl restart labgrid-exporter

# Or use Ansible from your workstation
ansible-playbook -i inventory playbook.yml --limit my-pi
```

## Auto-Updates

| Component | Method | Schedule | Rollback |
|-----------|--------|----------|----------|
| **Fedora CoreOS** | Zincati + rpm-ostree | Sundays 3am | Automatic |
| **Containers** | Podman auto-update | Daily 4am | Manual |

```bash
# Check OS update status
rpm-ostree status

# Force OS update now
sudo rpm-ostree upgrade

# Check container updates
sudo podman auto-update --dry-run
```

### Why this is safe

- **A/B partitions**: OS updates install to inactive partition
- **Auto-rollback**: If new OS fails to boot 3 times → reverts
- **Staged updates**: Zincati coordinates timing across fleet
- **No apt/dnf**: Can't accidentally break the system

## Minimal lab-config.yaml

```yaml
lab:
  hostname: labgrid-pi

ssh_keys:
  - ssh-ed25519 AAAA... you@host

# Add devices after first boot (easier to find serial paths)
devices: []
```

## Troubleshooting

### Won't boot
- Did you disable 3GB RAM limit in UEFI?
- Check ignition syntax: `butane --strict config.bu`

### Can't SSH
- Default user is `core` if no lab-config.yaml
- Check ignition was placed in `/mnt/ignition/config.ign`

### Find serial device paths
```bash
ls /dev/ttyUSB*
udevadm info /dev/ttyUSB0 | grep ID_PATH
```
