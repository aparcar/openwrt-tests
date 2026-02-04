CIP="192.168.1.3,192.168.1.100"
IFACE=en18
IMAGE=miwifi_r4a_firmware_72d65_2.28.62.bin
IMAGE=openwrt-ipq40xx-mikrotik-mikrotik_lhgg-60ad-initramfs-kernel.bin
IMAGE=openwrt-ipq40xx-mikrotik-mikrotik_lhgg-60ad-squashfs-sysupgrade.bin
IMAGE=openwrt-mediatek-mt7622-linksys_e8450-ubi-squashfs-sysupgrade.itb
IMAGE=openwrt-mvebu-cortexa9-cznic_turris-omnia-initramfs-kernel.bin

echo "interface=${IFACE}
        domain=unbrick.local
        dhcp-range=${CIP},2m
        dhcp-boot=${IMAGE}
        enable-tftp
        tftp-root=${PWD}" | tee /dev/stderr | dnsmasq -d -C -
