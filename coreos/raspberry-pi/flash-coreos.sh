#!/bin/bash
# Flash Fedora CoreOS to Raspberry Pi SD card
# Usage: ./flash-coreos.sh /dev/sdX [ignition-file]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COREOS_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    echo "Usage: $0 <device> [ignition-file]"
    echo ""
    echo "Flash Fedora CoreOS to SD card for Raspberry Pi 4/5"
    echo ""
    echo "Arguments:"
    echo "  device          Target device (e.g., /dev/sdb, /dev/mmcblk0)"
    echo "  ignition-file   Ignition config (default: generate from lab-config.yaml)"
    echo ""
    echo "Example:"
    echo "  $0 /dev/sdb"
    echo "  $0 /dev/sdb labnode.ign"
}

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root: sudo $0 $@${NC}"
    exit 1
fi

if [ $# -lt 1 ]; then
    usage
    exit 1
fi

DEVICE="$1"
IGNITION="${2:-}"

# Verify device exists and is a block device
if [ ! -b "$DEVICE" ]; then
    echo -e "${RED}Error: $DEVICE is not a block device${NC}"
    exit 1
fi

# Safety check - don't flash system disk
if mount | grep -q "^$DEVICE"; then
    echo -e "${RED}Error: $DEVICE appears to be mounted. Unmount first.${NC}"
    exit 1
fi

# Confirm
echo -e "${YELLOW}WARNING: This will ERASE ALL DATA on $DEVICE${NC}"
lsblk "$DEVICE"
echo ""
read -p "Are you sure? Type 'yes' to continue: " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 1
fi

# Generate ignition if not provided
if [ -z "$IGNITION" ]; then
    if [ -f "$COREOS_DIR/lab-config.yaml" ]; then
        echo -e "${GREEN}>>> Generating Ignition from lab-config.yaml${NC}"
        IGNITION="/tmp/labnode-$$.ign"

        # Check for dependencies
        if ! command -v python3 &> /dev/null; then
            echo -e "${RED}Error: python3 required${NC}"
            exit 1
        fi

        python3 -c "import yaml" 2>/dev/null || pip3 install --user pyyaml

        # Download butane if needed
        if ! command -v butane &> /dev/null; then
            echo "Downloading butane..."
            curl -sLo /tmp/butane https://github.com/coreos/butane/releases/download/v0.21.0/butane-aarch64-unknown-linux-gnu
            chmod +x /tmp/butane
            BUTANE=/tmp/butane
        else
            BUTANE=butane
        fi

        python3 "$COREOS_DIR/scripts/generate-butane.py" "$COREOS_DIR/lab-config.yaml" | \
            $BUTANE --strict > "$IGNITION"

        echo "Generated: $IGNITION"
    else
        echo -e "${RED}Error: No ignition file provided and no lab-config.yaml found${NC}"
        echo "Create lab-config.yaml first: cp lab-config.yaml.example lab-config.yaml"
        exit 1
    fi
fi

if [ ! -f "$IGNITION" ]; then
    echo -e "${RED}Error: Ignition file not found: $IGNITION${NC}"
    exit 1
fi

# Check for coreos-installer
if ! command -v coreos-installer &> /dev/null; then
    echo -e "${GREEN}>>> Installing coreos-installer${NC}"

    # Try package manager first
    if command -v dnf &> /dev/null; then
        dnf install -y coreos-installer
    elif command -v apt-get &> /dev/null; then
        # Use container for Debian/Ubuntu
        echo "Using podman/docker to run coreos-installer..."
        if command -v podman &> /dev/null; then
            CONTAINER_CMD="podman"
        elif command -v docker &> /dev/null; then
            CONTAINER_CMD="docker"
        else
            echo -e "${RED}Error: Install podman or docker, or run on Fedora${NC}"
            exit 1
        fi

        # Run coreos-installer via container
        echo -e "${GREEN}>>> Flashing Fedora CoreOS (aarch64) to $DEVICE${NC}"
        $CONTAINER_CMD run --rm --privileged \
            -v /dev:/dev \
            -v /run/udev:/run/udev \
            -v "$(dirname "$IGNITION"):/data:ro" \
            quay.io/coreos/coreos-installer:release \
            install "$DEVICE" \
            --architecture aarch64 \
            --ignition-file "/data/$(basename "$IGNITION")" \
            --append-karg nomodeset \
            --append-karg console=tty1

        # Skip to firmware setup
        INSTALLED_VIA_CONTAINER=1
    fi
fi

if [ -z "$INSTALLED_VIA_CONTAINER" ]; then
    echo -e "${GREEN}>>> Flashing Fedora CoreOS (aarch64) to $DEVICE${NC}"
    coreos-installer install "$DEVICE" \
        --architecture aarch64 \
        --ignition-file "$IGNITION" \
        --append-karg nomodeset \
        --append-karg console=tty1
fi

# Setup UEFI firmware for Raspberry Pi
echo -e "${GREEN}>>> Installing Raspberry Pi UEFI firmware${NC}"

# Find EFI partition
sleep 2  # Wait for kernel to re-read partition table
partprobe "$DEVICE" 2>/dev/null || true

if [[ "$DEVICE" == *"mmcblk"* ]] || [[ "$DEVICE" == *"nvme"* ]]; then
    EFI_PART="${DEVICE}p1"
else
    EFI_PART="${DEVICE}1"
fi

# Mount EFI partition
EFI_MOUNT="/tmp/efi-$$"
mkdir -p "$EFI_MOUNT"
mount "$EFI_PART" "$EFI_MOUNT"

# Download latest RPi4 UEFI firmware
UEFI_VERSION="v1.39"
UEFI_URL="https://github.com/pftf/RPi4/releases/download/${UEFI_VERSION}/RPi4_UEFI_Firmware_${UEFI_VERSION}.zip"

echo "Downloading UEFI firmware ${UEFI_VERSION}..."
curl -sL "$UEFI_URL" -o /tmp/rpi-uefi.zip
unzip -o /tmp/rpi-uefi.zip -d "$EFI_MOUNT/"

# Cleanup
umount "$EFI_MOUNT"
rmdir "$EFI_MOUNT"
rm /tmp/rpi-uefi.zip

echo ""
echo -e "${GREEN}=== Flash Complete ===${NC}"
echo ""
echo "Next steps:"
echo "1. Insert SD card into Raspberry Pi 4/5"
echo "2. Connect monitor and keyboard for first boot (UEFI setup)"
echo "3. In UEFI menu: Device Manager → Raspberry Pi Configuration"
echo "   - Advanced Configuration → Limit RAM to 3GB → Disabled"
echo "   - (For Pi 5: may need additional settings)"
echo "4. Save and exit UEFI, system will boot Fedora CoreOS"
echo ""
echo "SSH access: ssh labgrid@<ip-address>"
