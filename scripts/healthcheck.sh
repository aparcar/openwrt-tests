#!/usr/bin/env bash
#
# healthcheck.sh — run the healthcheck (test_shell + test_ssh) against a single
# real device in a lab, mirroring what .github/workflows/healthcheck.yml does.
#
# Usage:
#   ./scripts/healthcheck.sh <lab> <device>
#
#   <lab>     proxy name as in labnet.yaml, e.g. labgrid-aparcar
#   <device>  device name (a targets/<device>.yaml must exist), e.g. openwrt_one
#
# Environment:
#   RELEASE    OpenWrt release to fetch firmware for. Overrides the target's
#              openwrt.healthcheck_version; falls back to 25.12.5 if neither set.
#   LG_IMAGE   Skip the download and use this firmware image instead.
#
# Requires: uv, yq, jq, curl, wget and a working SSH config for the lab proxy
# (see docs/ and the healthcheck workflow for the coordinator/proxy setup).

set -euo pipefail

# Release to fetch firmware for. Precedence: explicit $RELEASE env >
# the target's openwrt.healthcheck_version > this default.
DEFAULT_RELEASE="25.12.5"
RELEASE="${RELEASE:-}"

die() { echo "healthcheck: $*" >&2; exit 1; }

# Print a variable and its current value (empty if unset).
show_env() { echo "healthcheck: env $1=${!1-}"; }

[ "$#" -eq 2 ] || die "usage: $0 <lab> <device>"

LAB="$1"
DEVICE="$2"
export TMPDIR=/tmp

# Run from the repository root (this script lives in scripts/).
cd "$(dirname "$0")/.."

for bin in uv yq jq curl wget; do
    command -v "$bin" >/dev/null 2>&1 || die "missing required tool: $bin"
done

TARGET_FILE="targets/${DEVICE}.yaml"
[ -f "$TARGET_FILE" ] || die "no such target file: $TARGET_FILE"

# Sanity-check that the device is actually declared for this lab in labnet.yaml.
if ! LAB="$LAB" DEVICE="$DEVICE" \
    yq -e '.labs[strenv(LAB)].devices[] | select(. == strenv(DEVICE))' \
    labnet.yaml >/dev/null 2>&1; then
    die "device '$DEVICE' is not listed under lab '$LAB' in labnet.yaml"
fi

export LG_PROXY="$LAB"
# NB: LG_ENV is exported only *after* the reservation is in place — the target
# YAML templates ${LG_PLACE}, so loading it before LG_PLACE is set would fail.

# labgrid reaches the coordinator via an SSH master to LG_PROXY (started with
# `-MN`, ControlPersist=300s) plus a `-W` ProxyJump child through the coordinator
# host. On a non-clean exit these orphan (reparent to PID 1) and keep the tunnel
# open, wedging the coordinator forward for the *next* run until they age out.
# Tear them down explicitly. The patterns match only labgrid's own tunnels
# (`-MN`/`-W`), never an interactive `ssh <proxy>`/`ssh <coordinator>` session.
LG_JUMP="$(ssh -G "$LG_PROXY" 2>/dev/null | awk '/^proxyjump /{print $2}')"

teardown_tunnels() {
    pkill -f "ssh .*-MN .*${LG_PROXY}" 2>/dev/null || true
    [ -n "$LG_JUMP" ] && pkill -f "ssh .*-W .*${LG_JUMP}" 2>/dev/null || true
}

# Clear any tunnels left behind by a previous crashed run before we start.
teardown_tunnels

# Resolve and download the release firmware unless one was provided explicitly.
if [ -z "${LG_IMAGE:-}" ]; then
    target=$(yq -r '.openwrt.target' "$TARGET_FILE")
    profile=$(yq -r '.openwrt.profile' "$TARGET_FILE")
    image_type=$(yq -r '.openwrt.image.type // "kernel"' "$TARGET_FILE")
    image_fs=$(yq -r '.openwrt.image.filesystem // ""' "$TARGET_FILE")

    [ "$target" != "null" ] || die "$TARGET_FILE has no openwrt.target"
    [ "$profile" != "null" ] || die "$TARGET_FILE has no openwrt.profile"

    # A target may pin a specific release for the healthcheck (e.g. a board that
    # regressed on newer OpenWrt); an explicit $RELEASE env still overrides it.
    hc_version=$(yq -r '.openwrt.healthcheck_version // ""' "$TARGET_FILE")
    release="${RELEASE:-${hc_version:-$DEFAULT_RELEASE}}"
    echo "healthcheck: release=$release"

    upstream_url="https://downloads.openwrt.org/releases/${release}/targets"
    profiles_json=$(curl -sf "$upstream_url/${target/-//}/profiles.json") \
        || die "failed to fetch profiles.json for $target ($release)"

    if [ -n "$image_fs" ]; then
        firmware_name=$(echo "$profiles_json" | jq -r \
            --arg p "$profile" --arg t "$image_type" --arg fs "$image_fs" \
            '.profiles[$p].images[]? | select(.type == $t and .filesystem == $fs) | .name' | head -n1)
    else
        firmware_name=$(echo "$profiles_json" | jq -r \
            --arg p "$profile" --arg t "$image_type" \
            '.profiles[$p].images[]? | select(.type == $t) | .name' | head -n1)
    fi

    [ -n "$firmware_name" ] && [ "$firmware_name" != "null" ] \
        || die "no matching firmware image for profile '$profile' (type=$image_type fs=${image_fs:-any})"

    download_dir="firmware"
    mkdir -p "$download_dir"
    # firmware_name already embeds release + device, so it is unique per image.
    final_path="$download_dir/${firmware_name%.gz}"

    if [ -f "$final_path" ]; then
        echo "healthcheck: reusing cached firmware $final_path"
    else
        echo "healthcheck: downloading $firmware_name ($release)"
        wget -q "$upstream_url/${target/-//}/$firmware_name" \
            --output-document "$download_dir/$firmware_name" \
            || die "failed to download $firmware_name"
        case "$firmware_name" in
            *.gz) gzip -df "$download_dir/$firmware_name" ;;
        esac
    fi

    export LG_IMAGE="$PWD/$final_path"
fi

show_env LG_PROXY
show_env LG_IMAGE

# Reserve a free place for this device (no LG_ENV yet — see note above), then
# always release on exit once we know the reservation token.
eval "$(uv run labgrid-client reserve --wait --shell "device=$DEVICE")"
[ -n "${LG_TOKEN:-}" ] || die "reservation failed: no LG_TOKEN returned"
export LG_PLACE="+"
show_env LG_TOKEN
show_env LG_PLACE

cleanup() {
    uv run labgrid-client power off || true
    uv run labgrid-client unlock || true
    uv run labgrid-client cancel-reservation "$LG_TOKEN" || true
    teardown_tunnels
}
trap cleanup EXIT

uv run labgrid-client -p "+$LG_TOKEN" lock

# Only now that LG_PLACE is set is it safe to load the target YAML.
export LG_ENV="$TARGET_FILE"
show_env LG_ENV

uv run pytest \
    tests/test_base.py::test_shell \
    tests/test_base.py::test_ssh \
    -v --lg-log --lg-colored-steps --log-cli-level=CONSOLE
