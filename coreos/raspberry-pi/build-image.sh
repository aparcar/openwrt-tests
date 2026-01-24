#!/bin/bash
# Generate Fedora CoreOS image for Raspberry Pi
# No root required - uses podman for image manipulation
# Usage: ./build-image.sh [config.ign] [-o output.img]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IGNITION=""
OUTPUT="labnode-rpi.img"

usage() {
    echo "Usage: $0 [config.ign] [-o output.img]"
    echo ""
    echo "Generate a ready-to-flash Fedora CoreOS image for Raspberry Pi"
    echo "No root required - uses podman for image manipulation"
    echo ""
    echo "Options:"
    echo "  config.ign   Ignition file (optional)"
    echo "  -o FILE      Output image (default: labnode-rpi.img)"
    echo ""
    echo "Example:"
    echo "  $0                           # Minimal image"
    echo "  $0 config.ign                # With ignition"
    echo "  $0 config.ign -o mylab.img   # Custom output name"
    echo ""
    echo "Then flash with:"
    echo "  sudo dd if=labnode-rpi.img of=/dev/sdX bs=4M status=progress"
    echo ""
    echo "Requires: podman (or docker), curl"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output)
            OUTPUT="$2"
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

# Find container runtime
if command -v podman &>/dev/null; then
    PODMAN="podman"
elif command -v docker &>/dev/null; then
    PODMAN="docker"
else
    echo "Error: podman or docker required"
    exit 1
fi

if ! command -v curl &>/dev/null; then
    echo "Error: curl required"
    exit 1
fi

# Resolve ignition path
if [ -n "$IGNITION" ]; then
    if [ ! -f "$IGNITION" ]; then
        echo "Error: Ignition file not found: $IGNITION"
        exit 1
    fi
    IGNITION=$(realpath "$IGNITION")
fi

OUTPUT=$(realpath "$OUTPUT")

WORK_DIR=$(mktemp -d)
trap "rm -rf $WORK_DIR" EXIT
cd "$WORK_DIR"

echo "=== Building Fedora CoreOS Image for Raspberry Pi ==="
echo "Output: $OUTPUT"
[ -n "$IGNITION" ] && echo "Ignition: $IGNITION"
echo ""

# 1. Download Fedora CoreOS
STREAM="stable"
ARCH="aarch64"
echo ">>> Downloading Fedora CoreOS ($STREAM, $ARCH)..."

META_URL="https://builds.coreos.fedoraproject.org/streams/${STREAM}.json"
IMAGE_URL=$(curl -sL "$META_URL" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d['architectures']['$ARCH']['artifacts']['metal']['formats']['raw.xz']['disk']['location'])
")

curl -L --progress-bar "$IMAGE_URL" -o fcos.raw.xz

echo ">>> Extracting image..."
xz -d fcos.raw.xz

# 2. Download UEFI firmware
echo ">>> Downloading Raspberry Pi UEFI firmware..."
UEFI_VERSION="v1.39"
curl -sL "https://github.com/pftf/RPi4/releases/download/${UEFI_VERSION}/RPi4_UEFI_Firmware_${UEFI_VERSION}.zip" -o uefi.zip
unzip -q uefi.zip -d uefi/

# 3. Create modification script for container
cat > modify.sh << 'SCRIPT'
#!/bin/bash
set -e

# Setup loop device
LOOP=$(losetup --find --show --partscan /work/fcos.raw)
trap "losetup -d $LOOP" EXIT

# Mount and modify EFI partition
mkdir -p /mnt/efi
mount "${LOOP}p1" /mnt/efi
cp -r /work/uefi/* /mnt/efi/
umount /mnt/efi

# Add ignition if provided
if [ -f /work/config.ign ]; then
    mkdir -p /mnt/boot
    mount "${LOOP}p2" /mnt/boot
    mkdir -p /mnt/boot/ignition
    cp /work/config.ign /mnt/boot/ignition/config.ign
    umount /mnt/boot
fi

echo "Image modified successfully"
SCRIPT
chmod +x modify.sh

# Copy ignition if provided
[ -n "$IGNITION" ] && cp "$IGNITION" config.ign

# 4. Run modification in container (needs privileges for losetup)
echo ">>> Modifying image (in container)..."

$PODMAN run --rm --privileged \
    -v "$WORK_DIR:/work:Z" \
    fedora:latest \
    /work/modify.sh

# 5. Move to output
mv fcos.raw "$OUTPUT"

echo ""
echo "=== Done! ==="
echo ""
echo "Image: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
echo ""
echo "Flash with:"
echo "  sudo dd if=$OUTPUT of=/dev/sdX bs=4M status=progress conv=fsync"
echo ""
echo "First boot: Press Esc for UEFI, disable 3GB RAM limit"
