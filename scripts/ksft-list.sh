#!/bin/sh
# Refresh tests/kselftest-net.list, the selftests test_kselftest_net.py runs,
# from the kselftest-list.txt of a built kselftests-net package: the image
# root (build_dir/target-*/root-*) or the package's ipkg-install directory.
#
#   sh scripts/ksft-list.sh <root>
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <root with usr/libexec/kselftests-net>" >&2; exit 2; }
src="$1/usr/libexec/kselftests-net/kselftest-list.txt"
[ -f "$src" ] || { echo "no $src" >&2; exit 1; }
cp "$src" "$(dirname "$0")/../tests/kselftest-net.list"
