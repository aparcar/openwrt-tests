#!/bin/bash
# Generate Ignition configuration from Butane files
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COREOS_DIR="$(dirname "$SCRIPT_DIR")"
IGNITION_DIR="$COREOS_DIR/ignition"

usage() {
    echo "Usage: $0 [OPTIONS] <butane-file>"
    echo ""
    echo "Generate Ignition JSON from Butane YAML configuration"
    echo ""
    echo "Options:"
    echo "  -o, --output FILE   Output file (default: <input>.ign)"
    echo "  -s, --strict        Enable strict mode (fail on warnings)"
    echo "  -h, --help          Show this help message"
    echo ""
    echo "Example:"
    echo "  $0 ignition/labnode-standalone.bu"
    echo "  $0 -o my-lab.ign ignition/labnode-standalone.bu"
}

OUTPUT=""
STRICT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -o|--output)
            OUTPUT="$2"
            shift 2
            ;;
        -s|--strict)
            STRICT="--strict"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            INPUT="$1"
            shift
            ;;
    esac
done

if [ -z "$INPUT" ]; then
    echo "ERROR: No input file specified"
    usage
    exit 1
fi

# Resolve input path
if [[ "$INPUT" != /* ]]; then
    INPUT="$COREOS_DIR/$INPUT"
fi

if [ ! -f "$INPUT" ]; then
    echo "ERROR: Input file not found: $INPUT"
    exit 1
fi

# Default output filename
if [ -z "$OUTPUT" ]; then
    OUTPUT="${INPUT%.bu}.ign"
fi

# Check if butane is installed
if ! command -v butane &> /dev/null; then
    echo "Butane not found. Installing..."

    # Detect architecture
    ARCH=$(uname -m)
    case $ARCH in
        x86_64) ARCH="x86_64" ;;
        aarch64) ARCH="aarch64" ;;
        *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
    esac

    # Download butane
    BUTANE_VERSION="v0.20.0"
    BUTANE_URL="https://github.com/coreos/butane/releases/download/${BUTANE_VERSION}/butane-${ARCH}-unknown-linux-gnu"

    echo "Downloading butane ${BUTANE_VERSION}..."
    curl -sLo /tmp/butane "$BUTANE_URL"
    chmod +x /tmp/butane
    BUTANE="/tmp/butane"
else
    BUTANE="butane"
fi

echo "=== Generating Ignition Configuration ==="
echo "Input:  $INPUT"
echo "Output: $OUTPUT"
echo ""

# Generate ignition
$BUTANE $STRICT --pretty "$INPUT" > "$OUTPUT"

echo "Successfully generated: $OUTPUT"
echo ""
echo "To use this configuration:"
echo "1. Download Fedora CoreOS: https://fedoraproject.org/coreos/download"
echo "2. Boot with: coreos-installer install /dev/sdX --ignition-file $OUTPUT"
echo "   Or for cloud: provide as user-data"
