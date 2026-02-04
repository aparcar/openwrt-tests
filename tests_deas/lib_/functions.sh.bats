#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

setup() {
    export IPKG_INSTROOT="$BATS_FILE_TMPDIR/rootfs"
    mkdir -p "$IPKG_INSTROOT/etc/"
    echo "wheel:*:0:root" >"$BATS_FILE_TMPDIR/rootfs/etc/group"
    echo "root:*:0:0:root:/var/root:/bin/sh" >"$BATS_FILE_TMPDIR/rootfs/etc/passwd"

    mkdir -p "$IPKG_INSTROOT/lib/apk/packages/"
    echo "lldp=121:lldp=129" > "$IPKG_INSTROOT/lib/apk/packages/lldpd.rusers"

    . $(pwd)/package/base-files/files/lib/functions.sh
}

# teardown() {
#     # rm -rf "$BATS_FILE_TMPDIR"
# }

@test "test append" {
    VAR="a b"
    append VAR "c"
    [ "$VAR" = "a b c" ]
    append VAR "d" "-"
    [ "$VAR" = "a b c-d" ]
}

@test "test prepend" {
    VAR="a b"
    prepend VAR "c"
    [ "$VAR" = "c a b" ]

    prepend VAR "d" "-"
    [ "$VAR" = "d-c a b" ]
}

@test "list contains" {
    VAR="a b c"
    run -0 list_contains VAR "a"
    run -1 list_contains VAR "d"

}

@test "get_bool" {
    [ "$(get_bool 0)" = 0 ]
    [ "$(get_bool off)" = 0 ]
    [ "$(get_bool false)" = 0 ]
    [ "$(get_bool no)" = 0 ]
    [ "$(get_bool disabled)" = 0 ]

    [ "$(get_bool 1)" = 1 ]
    [ "$(get_bool on)" = 1 ]
    [ "$(get_bool true)" = 1 ]
    [ "$(get_bool yes)" = 1 ]
    [ "$(get_bool enabled)" = 1 ]
}

@test "group_exists" {
    run -0 group_exists wheel
    run -1 group_exists not_existing
}

@test "user_exists" {
    run -0 user_exists root
    run -1 user_exists not_existing
}

@test "add_group_and_user" {
    export root="$IPKG_INSTROOT"
    run -0 add_group_and_user lldpd
    run -0 user_exists lldp
    run -0 group_exists lldp
    unset root
}

@test "user_add" {
    user_add test_user 123 123 description /var/lib/test /bin/fish
    run -0 user_exists test_user
}
