# Build an OpenWrt image that runs a labgrid coordinator and exporter.
#
#   make -C lab-image LAB=labgrid-aparcar PLATFORM=rpi-5
#
# The image is generated from labnet.yaml and ansible/files/exporter/<LAB>/
# using the OpenWrt ImageBuilder. See docs/openwrt-lab-image.md.

LAB ?=
PLATFORM ?= rpi-4
# OpenWrt release (e.g. 24.10.2) or "snapshot", defaults to the current stable
VERSION ?=
ifeq ($(VERSION),)
  VERSION := $(shell curl -sf https://downloads.openwrt.org/.versions.json | \
	python3 -c 'import json, sys; print(json.load(sys.stdin)["stable_version"])')
endif
# Python version of the OpenWrt python3 package (e.g. 3.13), detected if unset
PYTHON_VERSION ?=

LABGRID_SRC ?= git+https://github.com/aparcar/labgrid.git@aparcar/staging
ROOTFS_PARTSIZE ?= 1024
# directory merged on top of the generated files, e.g. for the SSH host key
# (etc/ssh/ssh_host_ed25519_key) or a WireGuard setup (etc/uci-defaults/)
FILES_EXTRA ?=
EXTRA_PACKAGES ?=
# set to 1 to skip fetching SSH keys from GitHub
OFFLINE ?=

PIP ?= uvx --from pip pip
PYTHON ?= uv run --no-project --with pyyaml python3

TOPDIR := $(abspath ..)
BUILD_DIR := $(CURDIR)/build
BIN_DIR ?= $(CURDIR)/bin/$(LAB)-$(PLATFORM)

ifeq ($(PLATFORM),x86-64)
  TARGET := x86/64
  PROFILE := generic
  PKG_ARCH := x86_64
  WHEEL_ARCH := x86_64
else ifeq ($(PLATFORM),rpi-3)
  TARGET := bcm27xx/bcm2710
  PROFILE := rpi-3
  PKG_ARCH := aarch64_cortex-a53
  WHEEL_ARCH := aarch64
else ifeq ($(PLATFORM),rpi-4)
  TARGET := bcm27xx/bcm2711
  PROFILE := rpi-4
  PKG_ARCH := aarch64_cortex-a72
  WHEEL_ARCH := aarch64
else ifeq ($(PLATFORM),rpi-5)
  TARGET := bcm27xx/bcm2712
  PROFILE := rpi-5
  PKG_ARCH := aarch64_cortex-a76
  WHEEL_ARCH := aarch64
else
  $(error Unknown PLATFORM '$(PLATFORM)', use x86-64, rpi-3, rpi-4 or rpi-5)
endif

ifeq ($(VERSION),snapshot)
  DOWNLOAD_URL := https://downloads.openwrt.org/snapshots
else
  DOWNLOAD_URL := https://downloads.openwrt.org/releases/$(VERSION)
endif

IB_DIR := $(BUILD_DIR)/ib-$(VERSION)-$(subst /,-,$(TARGET))
FILES_DIR := $(BUILD_DIR)/files-$(LAB)-$(PLATFORM)
WHEEL_DIR := $(BUILD_DIR)/wheels-$(VERSION)-$(WHEEL_ARCH)

PACKAGES := \
	python3 libstdcpp libudev-zero \
	-dropbear openssh-server openssh-sftp-server openssh-keygen \
	-procd-ujail \
	coreutils-stat ip-tiny rsync ser2net socat sudo uhttpd \
	wireguard-tools \
	kmod-usb-acm kmod-usb-serial-ch341 kmod-usb-serial-cp210x \
	kmod-usb-serial-ftdi kmod-usb-serial-pl2303 \
	kmod-usb-net-asix-ax88179 kmod-usb-net-cdc-ether kmod-usb-net-rtl8152 \
	$(EXTRA_PACKAGES)

.PHONY: image files wheels imagebuilder clean check-lab

image: files imagebuilder
	$(MAKE) -C $(IB_DIR) image \
		PROFILE="$(PROFILE)" \
		PACKAGES="$(PACKAGES)" \
		FILES="$(FILES_DIR)" \
		ROOTFS_PARTSIZE="$(ROOTFS_PARTSIZE)" \
		EXTRA_IMAGE_NAME="$(LAB)" \
		BIN_DIR="$(BIN_DIR)"
	@echo "Images for $(LAB) ($(PLATFORM), OpenWrt $(VERSION)) in $(BIN_DIR)"

check-lab:
	@[ -n "$(LAB)" ] || { echo "Set LAB, e.g. LAB=labgrid-aparcar"; exit 1; }
	@[ -n "$(VERSION)" ] || { echo "Could not detect VERSION, set e.g. VERSION=24.10.2"; exit 1; }

imagebuilder: check-lab $(IB_DIR)/.prepared

$(IB_DIR)/.prepared:
	mkdir -p $(IB_DIR)
	name=$$(curl -sf "$(DOWNLOAD_URL)/targets/$(TARGET)/sha256sums" | \
		sed -n 's/.*[ *]\(openwrt-imagebuilder-.*\.tar\.[a-z]*\)$$/\1/p'); \
	[ -n "$$name" ] || { echo "No ImageBuilder in $(DOWNLOAD_URL)/targets/$(TARGET)"; exit 1; }; \
	curl -fL "$(DOWNLOAD_URL)/targets/$(TARGET)/$$name" | \
		tar -x $$(case "$$name" in *.zst) echo --zstd ;; *.xz) echo -J ;; esac) \
			--strip-components=1 -C $(IB_DIR)
	touch $@

# labgrid itself is built from git, all dependencies are prebuilt musl wheels
# the wheels must match the Python version of the OpenWrt python3 package
wheels: check-lab
	rm -rf $(WHEEL_DIR)
	$(PIP) wheel --no-deps --wheel-dir $(WHEEL_DIR)/src "labgrid @ $(LABGRID_SRC)"
	pyver="$(PYTHON_VERSION)"; \
	[ -n "$$pyver" ] || pyver=$$(curl -sf "$(DOWNLOAD_URL)/packages/$(PKG_ARCH)/packages/index.json" | \
		python3 -c 'import json, sys; print(".".join(json.load(sys.stdin)["packages"]["python3-base"].split(".")[:2]))'); \
	[ -n "$$pyver" ] || { echo "Could not detect PYTHON_VERSION, set e.g. PYTHON_VERSION=3.13"; exit 1; }; \
	echo "Bundling labgrid for Python $$pyver ($(WHEEL_ARCH), musl)"; \
	$(PIP) install \
		--target $(WHEEL_DIR)/site \
		--implementation cp \
		--python-version $$pyver \
		--platform musllinux_1_2_$(WHEEL_ARCH) \
		--platform musllinux_1_1_$(WHEEL_ARCH) \
		--only-binary=:all: \
		--no-compile \
		$(WHEEL_DIR)/src/labgrid-*.whl
	# entry points carry the host's python shebang, files/usr/bin has wrappers
	rm -rf $(WHEEL_DIR)/site/bin

files: check-lab wheels
	rm -rf $(FILES_DIR)
	mkdir -p $(FILES_DIR)/usr/lib
	cp -a files/. $(FILES_DIR)/
	cp -a $(WHEEL_DIR)/site $(FILES_DIR)/usr/lib/labgrid
	cd $(TOPDIR) && $(PYTHON) lab-image/generate.py \
		--lab "$(LAB)" \
		--labnet labnet.yaml \
		--exporter-dir ansible/files/exporter \
		--out "$(FILES_DIR)" \
		$(if $(TRUNK),--trunk "$(TRUNK)") \
		$(if $(WAN),--wan "$(WAN)") \
		$(if $(OFFLINE),--offline)
	$(if $(FILES_EXTRA),cp -a $(abspath $(FILES_EXTRA))/. $(FILES_DIR)/)

clean:
	rm -rf $(BUILD_DIR) $(CURDIR)/bin
