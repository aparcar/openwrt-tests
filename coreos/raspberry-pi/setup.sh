#!/bin/bash
# OpenWrt Test Lab - Raspberry Pi Setup Script
# Run on a fresh Raspberry Pi OS Lite installation
set -e

echo "=== OpenWrt Test Lab Setup for Raspberry Pi ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

# Detect architecture
ARCH=$(uname -m)
echo "Architecture: $ARCH"

# Update system
echo ">>> Updating system..."
apt-get update
apt-get upgrade -y

# Install Docker
echo ">>> Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker pi 2>/dev/null || usermod -aG docker $SUDO_USER
fi

# Install docker-compose plugin
echo ">>> Installing Docker Compose..."
apt-get install -y docker-compose-plugin

# Install additional tools
echo ">>> Installing tools..."
apt-get install -y \
    git \
    socat \
    ser2net \
    vlan \
    iptables-persistent

# Enable IP forwarding
echo ">>> Enabling IP forwarding..."
cat > /etc/sysctl.d/99-labgrid.conf << 'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
sysctl -p /etc/sysctl.d/99-labgrid.conf

# Load 8021q module for VLANs
echo ">>> Enabling VLAN support..."
modprobe 8021q
echo "8021q" >> /etc/modules

# Create labgrid user
echo ">>> Creating labgrid user..."
if ! id labgrid &>/dev/null; then
    useradd -m -s /bin/bash -G dialout,plugdev,docker labgrid
fi

# Setup directory structure
echo ">>> Creating directory structure..."
LABDIR="/opt/labgrid"
mkdir -p $LABDIR/{config/labgrid,config/pdudaemon,config/dnsmasq,tftp}
chown -R labgrid:labgrid $LABDIR

# Copy docker-compose if in same directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
    cp "$SCRIPT_DIR/docker-compose.yml" $LABDIR/
fi

# Create default configs if they don't exist
if [ ! -f "$LABDIR/config/labgrid/exporter.yaml" ]; then
    cat > $LABDIR/config/labgrid/exporter.yaml << 'EOF'
# Labgrid exporter configuration
# Add your devices here. Example:
#
# my-device:
#   USBSerialPort:
#     match:
#       ID_PATH: platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1:1.0
#     speed: 115200
#   NetworkService:
#     address: 192.168.101.1%vlan101
#     username: root
#   PDUDaemonPort:
#     host: localhost:16421
#     pdu: 192.168.128.2
#     index: 1
EOF
fi

if [ ! -f "$LABDIR/config/pdudaemon/pdudaemon.conf" ]; then
    cat > $LABDIR/config/pdudaemon/pdudaemon.conf << 'EOF'
{
  "daemon": {
    "hostname": "0.0.0.0",
    "port": 16421,
    "logging_level": "INFO"
  },
  "pdus": {
  }
}
EOF
fi

if [ ! -f "$LABDIR/config/dnsmasq.conf" ]; then
    cat > $LABDIR/config/dnsmasq.conf << 'EOF'
port=0
enable-tftp
tftp-root=/srv/tftp
log-dhcp
conf-dir=/etc/dnsmasq.d/,*.conf
EOF
fi

# Create systemd service
echo ">>> Creating systemd service..."
cat > /etc/systemd/system/labgrid.service << EOF
[Unit]
Description=OpenWrt Test Lab Services
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$LABDIR
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
User=labgrid
Group=docker

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable labgrid.service

# Create helper script for serial device discovery
cat > /usr/local/bin/labgrid-find-serial << 'EOF'
#!/bin/bash
# Find USB serial devices and their ID_PATH
echo "=== USB Serial Devices ==="
for dev in /dev/ttyUSB* /dev/ttyACM* 2>/dev/null; do
    [ -e "$dev" ] || continue
    echo ""
    echo "Device: $dev"
    udevadm info "$dev" | grep -E "(ID_PATH|ID_SERIAL|ID_VENDOR|ID_MODEL)="
done
EOF
chmod +x /usr/local/bin/labgrid-find-serial

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Edit device configuration:"
echo "   sudo nano $LABDIR/config/labgrid/exporter.yaml"
echo ""
echo "2. Find your serial devices:"
echo "   labgrid-find-serial"
echo ""
echo "3. Start the services:"
echo "   sudo systemctl start labgrid"
echo "   # or: cd $LABDIR && docker compose up -d"
echo ""
echo "4. Check status:"
echo "   docker ps"
echo "   docker logs labgrid-exporter"
echo ""
