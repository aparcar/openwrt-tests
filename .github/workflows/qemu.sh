VERSION_NAME="24.10"
target=x86-64
UPSTREAM_URL="https://mirror-03.infra.openwrt.org/releases/24.10-SNAPSHOT/targets"
firmware=generic-squashfs-combined.img.gz

if [ "$VERSION_NAME" = "snapshot" ]; then
# Snapshot logic
firmware_name="openwrt-$target-$firmware"
wget "$UPSTREAM_URL/${target/-/\/}/$firmware_name" \
    --output-document "$firmware_name"
FIRMWARE_VERSION=$(curl "$UPSTREAM_URL/${target/-/\/}/version.buildinfo")
else
# Stable release logic
profiles_url="$UPSTREAM_URL/${target/-/\/}/profiles.json"
profiles_json=$(curl -s "$profiles_url")

# Find the appropriate image for QEMU
case "$firmware" in
    *squashfs-combined*)
    image_type="combined"
    filesystem="squashfs"
    ;;
    *ext4-combined*)
    image_type="combined"
    filesystem="ext4"
    ;;
    *initramfs*)
    image_type="kernel"
    filesystem=""
    ;;
    *vmlinux*)
    image_type="kernel"
    filesystem=""
    ;;
    *)
    image_type="combined"
    filesystem="squashfs"
    ;;
esac

if [ -n "$filesystem" ]; then
    firmware_name=$(echo "$profiles_json" | jq -r --arg type "$image_type" --arg fs "$filesystem" '
    .profiles.generic.images[] | select(.type == $type and .filesystem == $fs) | .name
    ')
else
    firmware_name=$(echo "$profiles_json" | jq -r --arg type "$image_type" '
    .profiles.generic.images[] | select(.type == $type) | .name
    ')
fi

if [ -z "$firmware_name" ] || [ "$firmware_name" = "null" ]; then
    echo "Could not find firmware, falling back to constructed name"
    image_prefix=$(echo "$profiles_json" | jq -r '.profiles.generic.image_prefix')
    firmware_name="$image_prefix-$firmware"
fi

echo "Using firmware: $firmware_name"
wget "$url_base/${target/-/\/}/$firmware_name" \
    --output-document "$firmware_name"
FIRMWARE_VERSION=$(echo "$profiles_json" | jq -r '.version_code')
fi

echo "FIRMWARE_VERSION=$FIRMWARE_VERSION"
echo "FIRMWARE_FILE=$firmware_name"
