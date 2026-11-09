#!/bin/sh
# Run openwrt-tests on one lab place: check nobody holds it, lock it, run
# pytest, then always power it off and unlock it (also on failure or Ctrl-C).
#
#   sh scripts/lab-pytest.sh <lab> <device> <image> [--reuse-own-lock] [pytest args...]
#   sh tests/scripts/lab-pytest.sh labgrid-aparcar openwrt_one \
#       /abs/openwrt-25.12.6-mediatek-filogic-openwrt_one-initramfs.itb \
#       tests/tests/test_wifi_clients.py -k clients_many -v --lg-log /tmp/lg
#
# <lab> is the lab's proxy (labnet.yaml), <device> the place suffix and target
# file (targets/<device>.yaml), <image> the boot image (RAM-booted by the
# strategy; nothing is flashed). Sets LG_PROXY, LG_PLACE, LG_ENV and LG_IMAGE,
# so callers don't need environment variables. Pytest arguments are passed
# on as they are (paths relative to the current directory). Exit code: pytest's,
# or 2 for a usage error, 3 if the place is held by someone.
#
# --reuse-own-lock (right after <image>): a lock this user holds from this host (left by an earlier
# run) is taken over instead of refused; it is released at the end like the
# script's own. Locks of other users or hosts are never touched.
set -eu

usage() {
	echo "usage: $0 <lab> <device> <image> [--reuse-own-lock] [pytest args...]" >&2
	exit 2
}
[ $# -ge 3 ] || usage
lab=$1
device=$2
image=$3
shift 3
# After the image, so "<script> <lab> ..." stays the command's prefix
# (permission and sandbox rules match on it)
reuse_own=""
if [ "${1:-}" = "--reuse-own-lock" ]; then
	reuse_own=1
	shift
fi

tests_dir=$(cd "$(dirname "$0")/.." && pwd)
case "$image" in
/*) ;;
*) image="$(pwd)/$image" ;;
esac
[ -f "$image" ] || { echo "no image $image" >&2; exit 2; }
target="$tests_dir/targets/$device.yaml"
[ -f "$target" ] || { echo "no target file $target" >&2; exit 2; }

place="$lab-$device"
client="$tests_dir/.venv/bin/labgrid-client"
export LG_PROXY="$lab" LG_PLACE="$place" LG_ENV="$target" LG_IMAGE="$image"
# macOS: ssh's ControlMaster socket path must stay short
export TMPDIR=/tmp
# labgrid-client holds a gRPC channel and forks ssh: without this, gRPC logs
# "skipping fork() handlers" on every fork (tests/conftest.py explains it)
export GRPC_ENABLE_FORK_SUPPORT=0

# labgrid names lock holders <user> on <host> (getpass.getuser(), gethostname())
me="$(id -un)@$(hostname)"
holder=$("$client" who | awk -v p="$place" '$3 == p { print $1 "@" $2 }')
if [ -n "$holder" ] && ! { [ -n "$reuse_own" ] && [ "$holder" = "$me" ]; }; then
	echo "$place is held by $holder: not touching it" >&2
	[ "$holder" = "$me" ] && echo "(your own lock: --reuse-own-lock takes it over)" >&2
	exit 3
fi

if [ -n "$holder" ]; then
	echo "== reusing your lock on $place"
else
	"$client" lock
fi
cleanup() {
	trap - EXIT INT TERM
	"$client" power off || echo "power off of $place failed" >&2
	"$client" unlock || echo "unlock of $place failed: run '$client -P $lab -p $place unlock'" >&2
}
trap cleanup EXIT
trap 'exit 130' INT TERM

echo "== $place locked, image $(basename "$image")"
rc=0
"$tests_dir/.venv/bin/pytest" -c "$tests_dir/pyproject.toml" "$@" || rc=$?
echo "== pytest exit code $rc"
exit "$rc"
