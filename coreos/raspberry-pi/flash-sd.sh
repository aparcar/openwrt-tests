#!/bin/bash
# Flash Fedora CoreOS to SD card for Raspberry Pi using podman
# Usage: ./flash-sd.sh /dev/sdX [config.ign]
set -e

DEVICE="${1:-}"
IGNITION="${2:-}"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

usage() {
    echo "Usage: $0 /dev/sdX [config.ign]"
    echo ""
    echo "Flash Fedora CoreOS to SD card for Raspberry Pi 4/5"
    echo ""
    echo "Arguments:"
    echo "  /dev/sdX      Target device (SD card)"
    echo "  config.ign    Ignition file (optional, generate with build-ignition.sh)"
    echo ""
    echo "Examples:"
    echo "  $0 /dev/sdb                    # Minimal install"
    echo "  $0 /dev/sdb config.ign         # With ignition config"
    echo ""
    echo "Requires: podman or docker"
}

if [ -z "$DEVICE" ]; then
    usage
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

# Find container runtime
if command -v podman &>/dev/null; then
    CONTAINER_CMD="podman"
elif command -v docker &>/dev/null; then
    CONTAINER_CMD="docker"
else
    echo -e "${RED}Error: podman or docker required${NC}"
    echo "Install with: sudo dnf install podman"
    exit 1
fi

# Safety check
echo -e "${RED}WARNING: This will ERASE ALL DATA on $DEVICE${NC}"
lsblk "$DEVICE"
echo ""
read -p "Type 'yes' to continue: " confirm
[ "$confirm" = "yes" ] || exit 1

# Build coreos-installer arguments
INSTALLER_ARGS=(
    install "$DEVICE"
    --architecture aarch64
    --append-karg nomodeset
    --append-karg console=tty1
)

# Add ignition if provided
if [ -n "$IGNITION" ]; then
    if [ ! -f "$IGNITION" ]; then
        echo -e "${RED}Ignition file not found: $IGNITION${NC}"
        exit 1
    fi
    IGNITION_ABS=$(realpath "$IGNITION")
    IGNITION_DIR=$(dirname "$IGNITION_ABS")
    IGNITION_FILE=$(basename "$IGNITION_ABS")
    INSTALLER_ARGS+=(--ignition-file "/data/$IGNITION_FILE")
    VOLUME_ARGS=(-v "$IGNITION_DIR:/data:ro")
else
    VOLUME_ARGS=()
    echo -e "${GREEN}No ignition file provided - will create minimal install${NC}"
    echo "You can add ignition later to /mnt/ignition/config.ign"
fi

# Flash using coreos-installer container
echo -e "${GREEN}>>> Flashing Fedora CoreOS (aarch64) to $DEVICE...${NC}"
echo "Using: $CONTAINER_CMD run quay.io/coreos/coreos-installer:release"
echo ""

$CONTAINER_CMD run --rm --privileged \
    -v /dev:/dev \
    -v /run/udev:/run/udev \
    "${VOLUME_ARGS[@]}" \
    quay.io/coreos/coreos-installer:release \
    "${INSTALLER_ARGS[@]}"

# Add Raspberry Pi UEFI firmware
echo ""
echo -e "${GREEN}>>> Adding Raspberry Pi UEFI firmware...${NC}"

sleep 2
partprobe "$DEVICE" 2>/dev/null || true
sleep 1

# Find EFI partition
if [[ "$DEVICE" == *"mmcblk"* ]] || [[ "$DEVICE" == *"nvme"* ]]; then
    EFI_PART="${DEVICE}p1"
else
    EFI_PART="${DEVICE}1"
fi

WORK_DIR=$(mktemp -d)
mount "$EFI_PART" "$WORK_DIR"

UEFI_VERSION="v1.39"
curl -sL "https://github.com/pftf/RPi4/releases/download/${UEFI_VERSION}/RPi4_UEFI_Firmware_${UEFI_VERSION}.zip" -o /tmp/uefi.zip
unzip -o /tmp/uefi.zip -d "$WORK_DIR/"
rm /tmp/uefi.zip

umount "$WORK_DIR"
rmdir "$WORK_DIR"

echo ""
echo -e "${GREEN}=== Done! ===${NC}"
echo ""
echo "Next steps:"
echo "1. Insert SD card into Raspberry Pi 4/5"
echo "2. First boot: Press Esc → UEFI setup"
echo "   Device Manager → Raspberry Pi Configuration → Advanced"
echo "   → Limit RAM to 3GB → Disabled"
echo "   → F10 save, Esc exit"
if [ -n "$IGNITION" ]; then
    echo "3. SSH: ssh labgrid@<ip>"
else
    echo "3. SSH: ssh core@<ip> (add your key to UEFI shell or use console)"
fi
