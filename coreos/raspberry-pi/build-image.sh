#!/bin/bash
# Prepare Fedora CoreOS files for Raspberry Pi
# Downloads everything, user does the privileged operations
# Usage: ./build-image.sh [config.ign] [-o output-dir]
set -e

IGNITION=""
OUTDIR="coreos-rpi"

usage() {
    echo "Usage: $0 [config.ign] [-o output-dir]"
    echo ""
    echo "Download and prepare Fedora CoreOS for Raspberry Pi"
    echo "No root/privileged operations - just downloads files"
    echo ""
    echo "Options:"
    echo "  config.ign   Ignition file (optional)"
    echo "  -o DIR       Output directory (default: coreos-rpi)"
    echo ""
    echo "Example:"
    echo "  $0 config.ign"
    echo "  $0 config.ign -o my-lab"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output)
            OUTDIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            IGNITION="$1"
            shift
            ;;
    esac
done

# Validate ignition if provided
if [ -n "$IGNITION" ] && [ ! -f "$IGNITION" ]; then
    echo "Error: Ignition file not found: $IGNITION"
    exit 1
fi

# Check for curl
if ! command -v curl &>/dev/null; then
    echo "Error: curl required"
    exit 1
fi

mkdir -p "$OUTDIR"
cd "$OUTDIR"

echo "=== Preparing Fedora CoreOS for Raspberry Pi ==="
echo "Output: $OUTDIR/"
echo ""

# 1. Download CoreOS
STREAM="stable"
ARCH="aarch64"
if [ ! -f fcos.raw.xz ]; then
    echo ">>> Downloading Fedora CoreOS ($STREAM, $ARCH)..."
    META_URL="https://builds.coreos.fedoraproject.org/streams/${STREAM}.json"
    IMAGE_URL=$(curl -sL "$META_URL" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d['architectures']['$ARCH']['artifacts']['metal']['formats']['raw.xz']['disk']['location'])
")
    curl -L --progress-bar "$IMAGE_URL" -o fcos.raw.xz
else
    echo ">>> Using cached fcos.raw.xz"
fi

# 2. Download UEFI firmware
UEFI_VERSION="v1.39"
if [ ! -d uefi ]; then
    echo ">>> Downloading Raspberry Pi UEFI firmware..."
    curl -sL "https://github.com/pftf/RPi4/releases/download/${UEFI_VERSION}/RPi4_UEFI_Firmware_${UEFI_VERSION}.zip" -o uefi.zip
    unzip -q uefi.zip -d uefi
    rm uefi.zip
else
    echo ">>> Using cached uefi/"
fi

# 3. Copy ignition if provided
if [ -n "$IGNITION" ]; then
    cp "$IGNITION" config.ign
    echo ">>> Copied ignition config"
fi

# 4. Generate flash script
cat > flash.sh << 'SCRIPT'
#!/bin/bash
set -e
DEVICE="${1:-}"

if [ -z "$DEVICE" ]; then
    echo "Usage: sudo ./flash.sh /dev/sdX"
    exit 1
fi

if [ "$EUID" -ne 0 ]; then
    echo "Run as root: sudo $0 $DEVICE"
    exit 1
fi

echo "=== Flashing to $DEVICE ==="
echo "WARNING: This will ERASE ALL DATA"
lsblk "$DEVICE"
read -p "Type 'yes' to continue: " confirm
[ "$confirm" = "yes" ] || exit 1

# Flash
echo ">>> Writing image..."
xzcat fcos.raw.xz | dd of="$DEVICE" bs=4M status=progress conv=fsync

sleep 2
partprobe "$DEVICE" 2>/dev/null || true
sleep 1

# Detect partition naming
if [[ "$DEVICE" == *"mmcblk"* ]] || [[ "$DEVICE" == *"nvme"* ]]; then
    P1="${DEVICE}p1"
    P2="${DEVICE}p2"
else
    P1="${DEVICE}1"
    P2="${DEVICE}2"
fi

# Add UEFI firmware
echo ">>> Adding UEFI firmware..."
mount "$P1" /mnt
cp -r uefi/* /mnt/
umount /mnt

# Add ignition if present
if [ -f config.ign ]; then
    echo ">>> Adding ignition config..."
    mount "$P2" /mnt
    mkdir -p /mnt/ignition
    cp config.ign /mnt/ignition/config.ign
    umount /mnt
fi

echo ""
echo "=== Done! ==="
echo "First boot: Press Esc for UEFI, disable 3GB RAM limit"
SCRIPT
chmod +x flash.sh

echo ""
echo "=== Ready ==="
echo ""
echo "Contents of $OUTDIR/:"
ls -lh
echo ""
echo "To flash, run:"
echo "  cd $OUTDIR"
echo "  sudo ./flash.sh /dev/sdX"
echo ""
echo "Or manually:"
echo "  xzcat fcos.raw.xz | sudo dd of=/dev/sdX bs=4M status=progress"
echo "  sudo mount /dev/sdX1 /mnt && sudo cp -r uefi/* /mnt/ && sudo umount /mnt"
[ -f config.ign ] && echo "  sudo mount /dev/sdX2 /mnt && sudo mkdir -p /mnt/ignition && sudo cp config.ign /mnt/ignition/ && sudo umount /mnt"
