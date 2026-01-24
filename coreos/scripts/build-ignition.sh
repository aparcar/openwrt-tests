#!/bin/bash
# Build Ignition configuration from simple lab config
# Usage: ./build-ignition.sh lab-config.yaml [output.ign]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COREOS_DIR="$(dirname "$SCRIPT_DIR")"

usage() {
    echo "Usage: $0 <lab-config.yaml> [output.ign]"
    echo ""
    echo "Generate Ignition configuration from lab config file."
    echo ""
    echo "Arguments:"
    echo "  lab-config.yaml   Lab configuration (see lab-config.yaml.example)"
    echo "  output.ign        Output file (default: labnode.ign)"
    echo ""
    echo "Example:"
    echo "  $0 lab-config.yaml"
    echo "  $0 my-lab.yaml my-lab.ign"
}

if [ $# -lt 1 ]; then
    usage
    exit 1
fi

CONFIG="$1"
OUTPUT="${2:-labnode.ign}"

if [ ! -f "$CONFIG" ]; then
    echo "Error: Config file not found: $CONFIG"
    exit 1
fi

# Check for required tools
check_tool() {
    if ! command -v "$1" &> /dev/null; then
        echo "Error: $1 not found. Please install it first."
        echo "  Fedora/RHEL: sudo dnf install $2"
        echo "  Debian/Ubuntu: sudo apt install $2"
        exit 1
    fi
}

check_tool python3 python3
check_tool pip3 python3-pip

# Install PyYAML if needed
if ! python3 -c "import yaml" 2>/dev/null; then
    echo "Installing PyYAML..."
    pip3 install --user pyyaml
fi

# Check for butane
if ! command -v butane &> /dev/null; then
    echo "Butane not found. Downloading..."
    ARCH=$(uname -m)
    case $ARCH in
        x86_64) ARCH="x86_64" ;;
        aarch64) ARCH="aarch64" ;;
        *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
    esac

    BUTANE_VERSION="v0.20.0"
    BUTANE_URL="https://github.com/coreos/butane/releases/download/${BUTANE_VERSION}/butane-${ARCH}-unknown-linux-gnu"
    curl -sLo /tmp/butane "$BUTANE_URL"
    chmod +x /tmp/butane
    BUTANE="/tmp/butane"
else
    BUTANE="butane"
fi

echo "=== Building Ignition Configuration ==="
echo "Config: $CONFIG"
echo "Output: $OUTPUT"
echo ""

# Generate Butane from config, then compile to Ignition
python3 "$SCRIPT_DIR/generate-butane.py" "$CONFIG" | $BUTANE --strict --pretty > "$OUTPUT"

echo "Success! Generated: $OUTPUT"
echo ""
echo "Next steps:"
echo "1. Download Fedora CoreOS from: https://fedoraproject.org/coreos/download"
echo "2. Install to disk:"
echo "   sudo coreos-installer install /dev/sdX --ignition-file $OUTPUT"
echo ""
echo "3. Or boot ISO/PXE and provide ignition via:"
echo "   - Kernel arg: ignition.config.url=http://server/$OUTPUT"
echo "   - USB drive:  copy to /ignition/config.ign on FAT partition"
