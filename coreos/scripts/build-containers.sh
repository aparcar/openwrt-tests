#!/bin/bash
# Build and optionally push container images for OpenWrt test lab
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COREOS_DIR="$(dirname "$SCRIPT_DIR")"
CONTAINERS_DIR="$COREOS_DIR/containers"

# Default registry
REGISTRY="${REGISTRY:-ghcr.io/openwrt/openwrt-tests}"
TAG="${TAG:-latest}"

# Container images to build
IMAGES=(
    "labgrid:Containerfile.labgrid"
    "pdudaemon:Containerfile.pdudaemon"
    "dnsmasq:Containerfile.dnsmasq"
    "ser2net:Containerfile.ser2net"
)

usage() {
    echo "Usage: $0 [OPTIONS] [IMAGE...]"
    echo ""
    echo "Build container images for OpenWrt test lab"
    echo ""
    echo "Options:"
    echo "  -p, --push     Push images to registry after building"
    echo "  -r, --registry Set container registry (default: $REGISTRY)"
    echo "  -t, --tag      Set image tag (default: $TAG)"
    echo "  -h, --help     Show this help message"
    echo ""
    echo "Images: labgrid, pdudaemon, dnsmasq, ser2net (default: all)"
}

PUSH=false
BUILD_IMAGES=()

while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--push)
            PUSH=true
            shift
            ;;
        -r|--registry)
            REGISTRY="$2"
            shift 2
            ;;
        -t|--tag)
            TAG="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            BUILD_IMAGES+=("$1")
            shift
            ;;
    esac
done

# If no specific images requested, build all
if [ ${#BUILD_IMAGES[@]} -eq 0 ]; then
    for img in "${IMAGES[@]}"; do
        BUILD_IMAGES+=("${img%%:*}")
    done
fi

# Find containerfile for image
get_containerfile() {
    local name="$1"
    for img in "${IMAGES[@]}"; do
        if [[ "${img%%:*}" == "$name" ]]; then
            echo "${img#*:}"
            return 0
        fi
    done
    return 1
}

echo "=== OpenWrt Test Lab Container Builder ==="
echo "Registry: $REGISTRY"
echo "Tag: $TAG"
echo "Push: $PUSH"
echo ""

cd "$CONTAINERS_DIR"

for name in "${BUILD_IMAGES[@]}"; do
    containerfile=$(get_containerfile "$name")
    if [ -z "$containerfile" ]; then
        echo "ERROR: Unknown image: $name"
        continue
    fi

    image_name="$REGISTRY/$name:$TAG"
    echo ">>> Building $image_name from $containerfile"

    podman build \
        -t "$image_name" \
        -f "$containerfile" \
        .

    if [ "$PUSH" = true ]; then
        echo ">>> Pushing $image_name"
        podman push "$image_name"
    fi

    echo ""
done

echo "=== Build complete ==="
