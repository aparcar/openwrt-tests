 # Get versions (reuse from previous step to avoid repeated API calls)
versions_json=$(curl -s https://downloads.openwrt.org/.versions.json)
stable_version=$(echo "$versions_json" | jq -r '.stable_version')
oldstable_version=$(echo "$versions_json" | jq -r '.oldstable_version')
stable_branch=$(echo "$stable_version" | cut -d. -f1,2)
oldstable_branch=$(echo "$oldstable_version" | cut -d. -f1,2)

versions="[
{\"type\": \"snapshot\", \"name\": \"snapshot\", \"version_url\": \"https://mirror-03.infra.openwrt.org/snapshots/targets\"},
{\"type\": \"stable\", \"name\": \"$stable_branch\", \"version\": \"$stable_version\", \"version_url\": \"https://mirror-03.infra.openwrt.org/releases/$stable_branch-SNAPSHOT/targets\"},
{\"type\": \"stable\", \"name\": \"$oldstable_branch\", \"version\": \"$oldstable_version\", \"version_url\": \"https://mirror-03.infra.openwrt.org/releases/$oldstable_branch-SNAPSHOT/targets\"}
]"

device_matrix=$(yq -o=json '
. as $root |
$root.labs as $labs |
$root.devices as $devices |
$labs
| to_entries
| map(
    .key as $lab |
    .value.devices
    | map(
        select($devices[.] != null) |
        {
            "device": .,
            "name": $devices[.].name,
            "proxy": $labs[$lab].proxy,
            "target": $devices[.].target,
            "firmware": $devices[.].firmware,
            "maintainers": $labs[$lab].maintainers,
            "snapshots_only": ($devices[.].snapshots_only // false)
        }
        )
    )
| flatten
' labnet.yaml)


echo $device_matrix

exit 0

# Combine devices with versions to create full matrix
matrix=$(echo "$device_matrix" | jq --argjson versions "$versions" '
[.[] as $device | $versions[] as $version | $device + {"version_url": $version.version_url, "version_name": $version.name}]
')
echo "matrix=$(echo "$matrix" | jq -c '.')" >> $GITHUB_ENV

# Create QEMU matrix
qemu_base='[
{"target": "malta-be", "firmware": "vmlinux-initramfs.elf", "dependency": "qemu-system-mips"},
{"target": "x86-64", "firmware": "generic-squashfs-combined.img.gz", "dependency": "qemu-system-x86"},
{"target": "armsr-armv8", "firmware": "generic-initramfs-kernel.bin", "dependency": "qemu-system-aarch64"}
]'
qemu_matrix=$(echo "$qemu_base" | jq --argjson versions "$versions" '
[.[] as $qemu | $versions[] as $version | $qemu + {"version_url": $version.version_url, "version_name": $version.name}]
')
echo "qemu_matrix=$(echo "$qemu_matrix" | jq -c '.')" >> $GITHUB_ENV
