LG_QEMU_BIN=qemu-system-x86_64 \
		poetry -C ~/src/openwrt/tests/ run pytest tests/ \
		--lg-env tests/qemu.yaml --lg-log -vv --lg-colored-steps \
	--target x86-64 \
	--firmware ../../openwrt/bin/targets/x86/64/openwrt-x86-64-generic-squashfs-combined.img
