#!/bin/bash
# Simple Fedora CoreOS flash for Raspberry Pi
# Usage: ./flash-sd.sh /dev/sdX [lab-config.yaml]
set -e

DEVICE="${1:-}"
CONFIG="${2:-../lab-config.yaml}"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

if [ -z "$DEVICE" ]; then
    echo "Usage: $0 /dev/sdX [lab-config.yaml]"
    echo ""
    echo "Steps this script performs:"
    echo "  1. Downloads Fedora CoreOS aarch64 image"
    echo "  2. Flashes to SD card"
    echo "  3. Adds your ignition config"
    echo "  4. Adds Raspberry Pi UEFI firmware"
    exit 1
fi

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Run as root: sudo $0 $@${NC}"
    exit 1
fi

if [ ! -b "$DEVICE" ]; then
    echo -e "${RED}$DEVICE is not a block device${NC}"
    exit 1
fi

# Safety check
echo -e "${RED}WARNING: This will ERASE $DEVICE${NC}"
lsblk "$DEVICE"
read -p "Type 'yes' to continue: " confirm
[ "$confirm" = "yes" ] || exit 1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="/tmp/coreos-flash-$$"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# 1. Download Fedora CoreOS
STREAM="stable"
ARCH="aarch64"
echo -e "${GREEN}>>> Downloading Fedora CoreOS ($STREAM, $ARCH)...${NC}"

# Get latest image URL from stream metadata
META_URL="https://builds.coreos.fedoraproject.org/streams/${STREAM}.json"
IMAGE_URL=$(curl -sL "$META_URL" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d['architectures']['$ARCH']['artifacts']['metal']['formats']['raw.xz']['disk']['location'])
")

IMAGE_FILE="fcos.raw.xz"
if [ ! -f "$IMAGE_FILE" ]; then
    curl -L "$IMAGE_URL" -o "$IMAGE_FILE"
fi

# 2. Flash to SD card
echo -e "${GREEN}>>> Flashing to $DEVICE...${NC}"
xzcat "$IMAGE_FILE" | dd of="$DEVICE" bs=4M status=progress conv=fsync

# Wait for partitions
sleep 2
partprobe "$DEVICE" 2>/dev/null || true
sleep 2

# Find partitions
if [[ "$DEVICE" == *"mmcblk"* ]] || [[ "$DEVICE" == *"nvme"* ]]; then
    EFI_PART="${DEVICE}p1"
    BOOT_PART="${DEVICE}p2"
else
    EFI_PART="${DEVICE}1"
    BOOT_PART="${DEVICE}2"
fi

# 3. Add Ignition config
echo -e "${GREEN}>>> Adding Ignition configuration...${NC}"

BOOT_MOUNT="$WORK_DIR/boot"
mkdir -p "$BOOT_MOUNT"
mount "$BOOT_PART" "$BOOT_MOUNT"

mkdir -p "$BOOT_MOUNT/ignition"

# Generate ignition from lab-config.yaml if it exists
if [ -f "$SCRIPT_DIR/$CONFIG" ] || [ -f "$CONFIG" ]; then
    CONFIG_PATH="$CONFIG"
    [ -f "$SCRIPT_DIR/$CONFIG" ] && CONFIG_PATH="$SCRIPT_DIR/$CONFIG"

    echo "Generating ignition from: $CONFIG_PATH"

    # Ensure dependencies
    python3 -c "import yaml" 2>/dev/null || pip3 install --quiet pyyaml

    # Get butane
    if ! command -v butane &>/dev/null; then
        curl -sLo /tmp/butane https://github.com/coreos/butane/releases/download/v0.21.0/butane-x86_64-unknown-linux-gnu
        chmod +x /tmp/butane
        BUTANE=/tmp/butane
    else
        BUTANE=butane
    fi

    python3 "$SCRIPT_DIR/../scripts/generate-butane.py" "$CONFIG_PATH" | \
        $BUTANE --strict > "$BOOT_MOUNT/ignition/config.ign"
else
    echo -e "${RED}No config found. Creating minimal ignition...${NC}"
    echo "You'll need to add SSH keys manually!"

    cat > "$BOOT_MOUNT/ignition/config.ign" << 'EOF'
{
  "ignition": { "version": "3.4.0" },
  "passwd": {
    "users": [{
      "name": "core",
      "groups": ["wheel", "sudo", "dialout"]
    }]
  }
}
EOF
fi

umount "$BOOT_MOUNT"

# 4. Add RPi UEFI firmware
echo -e "${GREEN}>>> Adding Raspberry Pi UEFI firmware...${NC}"

EFI_MOUNT="$WORK_DIR/efi"
mkdir -p "$EFI_MOUNT"
mount "$EFI_PART" "$EFI_MOUNT"

UEFI_VERSION="v1.39"
curl -sL "https://github.com/pftf/RPi4/releases/download/${UEFI_VERSION}/RPi4_UEFI_Firmware_${UEFI_VERSION}.zip" -o uefi.zip
unzip -o uefi.zip -d "$EFI_MOUNT/"

umount "$EFI_MOUNT"

# Cleanup
cd /
rm -rf "$WORK_DIR"

echo ""
echo -e "${GREEN}=== Done! ===${NC}"
echo ""
echo "Next steps:"
echo "1. Insert SD card into Raspberry Pi 4/5"
echo "2. First boot: Press Esc for UEFI setup"
echo "   → Device Manager → Raspberry Pi Configuration → Advanced"
echo "   → Limit RAM to 3GB → Disabled"
echo "   → F10 to save, Esc to exit"
echo "3. SSH: ssh core@<ip> (or labgrid@ if you used lab-config.yaml)"
