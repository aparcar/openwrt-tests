
curdir:=tests

OPENWRT_CI_TESTS = \
	$(curdir)/x86-64 \
	$(curdir)/armsr-armv8 \
	$(curdir)/malta-be \
	$(curdir)/shell

test: $(OPENWRT_CI_TESTS)

TESTSDIR ?= $(shell readlink -f $(TOPDIR)/tests)

define pytest
	poetry -C $(TESTSDIR) run \
		pytest $(TESTSDIR)/tests/ \
		--verbose \
		--lg-log \
		--lg-colored-steps $(if $(K),-k $(K),)
endef

$(curdir)/setup:
	@[ -n "$$(command -v poetry)" ] || \
		(echo "Please install poetry. See https://python-poetry.org/docs/#installation" && exit 1)
	@[ -n "$$(command -v bats)" ] || \
		(echo "Please install bats. See https://bats-core.readthedocs.io/en/stable/installation.html" && exit 1)
	@[ -n "$$(command -v qemu-system-mips)" ] || \
		(echo "Please install qemu-system-mips" && exit 1)
	@[ -n "$$(command -v qemu-system-x86_64)" ] || \
		(echo "Please install qemu-system-x86_64" && exit 1)
	@[ -n "$$(command -v qemu-system-aarch64)" ] || \
		(echo "Please install qemu-system-aarch64" && exit 1)
	@poetry -C $(TESTSDIR) install


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

define qemu_malta
	[ -f $(FIRMWARE) ]

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(pytest) \
		--lg-env $(TESTSDIR)/targets/qemu-malta.yaml \
		--firmware $(FIRMWARE)
endef


$(curdir)/malta-be: QEMU_BIN ?= qemu-system-mips
$(curdir)/malta-be: FIRMWARE ?= $(TOPDIR)/bin/targets/malta/be/openwrt-malta-be-vmlinux-initramfs.elf
$(curdir)/malta-be:
	$(qemu_malta)

$(curdir)/malta-le: QEMU_BIN ?= qemu-system-mipsel
$(curdir)/malta-le: FIRMWARE ?= $(TOPDIR)/bin/targets/malta/le/openwrt-malta-le-vmlinux-initramfs.elf
$(curdir)/malta-le:
	$(qemu_malta)

$(curdir)/malta-be64: QEMU_BIN ?= qemu-system-mips64
$(curdir)/malta-be64: FIRMWARE ?= $(TOPDIR)/bin/targets/malta/be64/openwrt-malta-be64-vmlinux-initramfs.elf
$(curdir)/malta-be64:
	$(qemu_malta)

$(curdir)/malta-le64: QEMU_BIN ?= qemu-system-mips64el
$(curdir)/malta-le64: FIRMWARE ?= $(TOPDIR)/bin/targets/malta/le64/openwrt-malta-le64-vmlinux-initramfs.elf
$(curdir)/malta-le64:
	$(qemu_malta)

$(curdir)/shell:
	[ -n "$$(command -v bats)" ] || (echo "Please install bats" && exit 1)
	bats -r $(TESTSDIR)/tests/bats

