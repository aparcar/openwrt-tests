
curdir:=tests

OPENWRT_CI_TESTS = \
	$(curdir)/x86-64 \
	$(curdir)/armsr-armv8 \
	$(curdir)/malta-be \
	$(curdir)/sifiveu-generic \
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

# Boot the target and attach to its serial console instead of running tests,
# e.g. `make tests/x86-64 CONSOLE=1`. Leave the console with Ctrl-].
define qemu_console
	uv --project $(TESTSDIR) run \
		$(TESTSDIR)/scripts/qemu-shell.py $(if $(V),--verbose,)
endef

run = $(if $(CONSOLE),$(qemu_console),$(pytest))

$(curdir)/setup:
	@[ -n "$$(command -v uv)" ] || \
		(echo "Please install uv. See https://docs.astral.sh/uv/" && exit 1)
	@[ -n "$$(command -v qemu-system-mips)" ] || \
		(echo "Please install qemu-system-mips" && exit 1)
	@[ -n "$$(command -v qemu-system-x86_64)" ] || \
		(echo "Please install qemu-system-x86_64" && exit 1)
	@[ -n "$$(command -v qemu-system-aarch64)" ] || \
		(echo "Please install qemu-system-aarch64" && exit 1)
	@[ -n "$$(command -v qemu-system-riscv64)" ] || \
		(echo "Please install qemu-system-riscv64" && exit 1)
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
		$(run) \
		--lg-env $(TESTSDIR)/targets/qemu_x86-64.yaml \
		--firmware $(FIRMWARE:.gz=)

# EFI disk boot. conftest.py finds the UEFI firmware (edk2/OVMF) in the usual
# QEMU and Debian paths; pass BIOS=/path/to/code.fd to override it.
efi_bios = $(if $(BIOS),--bios $(BIOS),)

$(curdir)/x86-64-efi: QEMU_BIN ?= qemu-system-x86_64
$(curdir)/x86-64-efi: FIRMWARE ?= $(TOPDIR)/bin/targets/x86/64/openwrt-x86-64-generic-squashfs-combined-efi.img.gz
$(curdir)/x86-64-efi:
	[ -f $(FIRMWARE) ]

	gzip \
		--force \
		--keep \
		--decompress \
		$(FIRMWARE) || true

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(run) \
		--lg-env $(TESTSDIR)/targets/qemu_x86-64-efi.yaml \
		--firmware $(FIRMWARE:.gz=) $(efi_bios)

$(curdir)/x86-generic-efi: QEMU_BIN ?= qemu-system-i386
$(curdir)/x86-generic-efi: FIRMWARE ?= $(TOPDIR)/bin/targets/x86/generic/openwrt-x86-generic-generic-squashfs-combined-efi.img.gz
$(curdir)/x86-generic-efi:
	[ -f $(FIRMWARE) ]

	gzip \
		--force \
		--keep \
		--decompress \
		$(FIRMWARE) || true

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(run) \
		--lg-env $(TESTSDIR)/targets/qemu_x86-generic-efi.yaml \
		--firmware $(FIRMWARE:.gz=) $(efi_bios)

$(curdir)/armsr-armv7-efi: QEMU_BIN ?= qemu-system-arm
$(curdir)/armsr-armv7-efi: FIRMWARE ?= $(TOPDIR)/bin/targets/armsr/armv7/openwrt-armsr-armv7-generic-squashfs-combined-efi.img.gz
$(curdir)/armsr-armv7-efi:
	[ -f $(FIRMWARE) ]

	gzip \
		--force \
		--keep \
		--decompress \
		$(FIRMWARE) || true

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(run) \
		--lg-env $(TESTSDIR)/targets/qemu_armsr-armv7-efi.yaml \
		--firmware $(FIRMWARE:.gz=) $(efi_bios)

$(curdir)/armsr-armv8-efi: QEMU_BIN ?= qemu-system-aarch64
$(curdir)/armsr-armv8-efi: FIRMWARE ?= $(TOPDIR)/bin/targets/armsr/armv8/openwrt-armsr-armv8-generic-squashfs-combined-efi.img.gz
$(curdir)/armsr-armv8-efi:
	[ -f $(FIRMWARE) ]

	gzip \
		--force \
		--keep \
		--decompress \
		$(FIRMWARE) || true

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(run) \
		--lg-env $(TESTSDIR)/targets/qemu_armsr-armv8-efi.yaml \
		--firmware $(FIRMWARE:.gz=) $(efi_bios)

$(curdir)/armsr-armv8: QEMU_BIN ?= qemu-system-aarch64
$(curdir)/armsr-armv8: FIRMWARE ?= $(TOPDIR)/bin/targets/armsr/armv8/openwrt-armsr-armv8-generic-initramfs-kernel.bin
$(curdir)/armsr-armv8:
	[ -f $(FIRMWARE) ]

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(run) \
		--lg-env $(TESTSDIR)/targets/qemu_armsr-armv8.yaml \
		--firmware $(FIRMWARE)

$(curdir)/malta-be: QEMU_BIN ?= qemu-system-mips
$(curdir)/malta-be: FIRMWARE ?= $(TOPDIR)/bin/targets/malta/be/openwrt-malta-be-generic-initramfs-kernel.bin
$(curdir)/malta-be:
	[ -f $(FIRMWARE) ]

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(run) \
		--lg-env $(TESTSDIR)/targets/qemu_malta-be.yaml \
		--firmware $(FIRMWARE)

$(curdir)/sifiveu-generic: QEMU_BIN ?= qemu-system-riscv64
$(curdir)/sifiveu-generic: FIRMWARE ?= $(TOPDIR)/bin/targets/sifiveu/generic/openwrt-sifiveu-generic-sifive_unleashed-initramfs-kernel.bin
$(curdir)/sifiveu-generic:
	[ -f $(FIRMWARE) ]

	LG_QEMU_BIN=$(QEMU_BIN) \
		$(run) \
		--lg-env $(TESTSDIR)/targets/qemu_sifiveu-generic.yaml \
		--firmware $(FIRMWARE)
