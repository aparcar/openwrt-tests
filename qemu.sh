 /opt/homebrew/bin/qemu-system-x86_64 \
	 ../openwrt/bin/targets/x86/64/openwrt-x86-64-generic-squashfs-combined.img \
	 -netdev user,id=n1,ipv6=off -device virtio-net-pci,netdev=n1 \
	 -nic user,model=virtio-net-pci,net=192.168.1.0/24,id=lan \
	 -device virtio-rng-pci \
	 -nographic
