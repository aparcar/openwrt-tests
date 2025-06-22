
curdir:=tests

OPENWRT_CI_TESTS = \
	$(curdir)/x86-64 \
	$(curdir)/armsr-armv8 \
	$(curdir)/malta-be \
	$(curdir)/shell

test: $(OPENWRT_CI_TESTS)

TESTSDIR ?= $(shell readlink -f $(TOPDIR)/tests)

define pytest
	uv --project $(TESTSDIR) run \
		pytest $(TESTSDIR)/tests/ \
		--lg-log \
		--log-cli-level=CONSOLE \
		--lg-colored-steps $(if $(K),-k $(K),)
endef

$(curdir)/setup:
	@[ -n "$$(command -v uv)" ] || \
		(echo "Please install uv. See https://docs.astral.sh/uv/" && exit 1)
	@[ -n "$$(command -v qemu-system-mips)" ] || \
		(echo "Please install qemu-system-mips" && exit 1)
	@[ -n "$$(command -v qemu-system-x86_64)" ] || \
		(echo "Please install qemu-system-x86_64" && exit 1)
	@[ -n "$$(command -v qemu-system-aarch64)" ] || \
		(echo "Please install qemu-system-aarch64" && exit 1)
	@uv --project $(TESTSDIR) sync


$(curdir)/x86-64: QEMU_BIN ?= qemu-system-x86_64
$(curdir)/x86-64: FIRMWARE ?= $(TOPDIR)/bin/targets/x86/64/openwrt-x86-64-generic-squashfs-combined.img.gz
$(curdir)/x86-64:

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

$(curdir)/armsr-armv8: QEMU_BIN ?= qemu-system-aarch64
$(curdir)/armsr-armv8: FIRMWARE ?= $(TOPDIR)/bin/targets/armsr/armv8/openwrt-armsr-armv8-generic-initramfs-kernel.bin
$(curdir)/armsr-armv8:
	[ -f $(FIRMWARE) ]

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(pytest) \
		--lg-env $(TESTSDIR)/targets/qemu-armsr-armv8.yaml \
		--firmware $(FIRMWARE)

$(curdir)/malta-be: QEMU_BIN ?= qemu-system-mips
$(curdir)/malta-be: FIRMWARE ?= $(TOPDIR)/bin/targets/malta/be/openwrt-malta-be-vmlinux-initramfs.elf
$(curdir)/malta-be:
	[ -f $(FIRMWARE) ]

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(pytest) \
		--lg-env $(TESTSDIR)/targets/qemu-malta-be.yaml \
		--firmware $(FIRMWARE)
