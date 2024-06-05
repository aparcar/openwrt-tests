OPENWRT_CI_TESTS = \
	test-x86-64 \
	test-armsr-armv8 \
	test-malta-be \
	test-shell

test: $(OPENWRT_CI_TESTS)

TESTSDIR ?= $(shell dirname $(shell readlink -f $(TOPDIR)/Makefile.tests))

define pytest
	poetry -C $(TESTSDIR) run \
		pytest $(TESTSDIR)/tests/ \
		--lg-log \
		--lg-colored-steps
endef

test-x86-64: QEMU_BIN ?= qemu-system-x86_64
test-x86-64: FIRMWARE ?= $(TOPDIR)/bin/targets/x86/64/openwrt-x86-64-generic-squashfs-combined.img.gz
test-x86-64:

	[ -f $(FIRMWARE) ]

	gzip \
		--force \
		--keep \
		--decompress \
		$(FIRMWARE) || true

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(pytest) \
		--lg-env $(TESTSDIR)/targets/qemu-x86-64.yaml \
		--firmware $(FIRMWARE:.gz=)

test-armsr-armv8: QEMU_BIN ?= qemu-system-aarch64
test-armsr-armv8: FIRMWARE ?= $(TOPDIR)/bin/targets/armsr/armv8/openwrt-armsr-armv8-generic-initramfs-kernel.bin
test-armsr-armv8:
	[ -f $(FIRMWARE) ]

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(pytest) \
		--lg-env $(TESTSDIR)/targets/qemu-armsr-armv8.yaml \
		--firmware $(FIRMWARE)

test-malta-be: QEMU_BIN ?= qemu-system-mips
test-malta-be: FIRMWARE ?= $(TOPDIR)/bin/targets/malta/be/openwrt-malta-be-vmlinux-initramfs.elf
test-malta-be:
	[ -f $(FIRMWARE) ]

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(pytest) \
		--lg-env $(TESTSDIR)/targets/qemu-malta-be.yaml \
		--firmware $(FIRMWARE)

test-shell:
	[ -n "$$(command -v bats)" ] || (echo "Please install bats" && exit 1)
	bats -r $(TESTSDIR)/tests/bats

