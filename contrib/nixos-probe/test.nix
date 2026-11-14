# NixOS VM test: the probe and a stand-in DUT on one trunk.
#
# The DUT is a Linux router like OpenWrt's: odhcp6c (OpenWrt's DHCPv6
# client) on its WAN VLAN asks for an address and a prefix, it announces the
# first /64 of the delegated prefix on its LAN VLAN (odhcpd's part, here
# radvd). Run: nix build .#checks.x86_64-linux.vm -L
self:
{ lib, pkgs, ... }:
let
  lanVlan = 101;
  wanVlan = 201;
  odhcp6cScript = pkgs.writeShellScript "odhcp6c-script" ''
    # $1: state (bound, updated, ...); keep what the test reads
    PATH=${lib.makeBinPath [ pkgs.coreutils pkgs.gnugrep ]}
    env | grep -E '^(ADDRESSES|PREFIXES|RA_ROUTES)=' > /run/odhcp6c.env.new
    echo "STATE=$1" >> /run/odhcp6c.env.new
    mv /run/odhcp6c.env.new /run/odhcp6c.env
  '';
in
{
  name = "nixos-probe";

  nodes = {
    probe = {
      imports = [ self.nixosModules.default ];
      virtualisation.vlans = [ 1 ];
      services.nixos-probe = {
        enable = true;
        trunk = "eth1";
      };
    };

    dut = {
      virtualisation.vlans = [ 1 ];
      networking.vlans = {
        lan.interface = "eth1";
        lan.id = lanVlan;
        wan.interface = "eth1";
        wan.id = wanVlan;
      };
      networking.interfaces.lan.ipv4.addresses = [
        {
          address = "192.168.1.1";
          prefixLength = 24;
        }
      ];
      boot.kernel.sysctl = {
        "net.ipv6.conf.all.forwarding" = 1;
        # a router still takes its default route from the upstream's RAs
        "net.ipv6.conf.wan.accept_ra" = 2;
      };
      networking.firewall.enable = false;
      # odhcp6c needs the DHCPv6 client port
      networking.dhcpcd.enable = false;
      environment.systemPackages = [
        pkgs.odhcp6c
        pkgs.radvd
      ];
      environment.etc."odhcp6c-script".source = odhcp6cScript;
    };
  };

  testScript = ''
    import ipaddress
    import json

    def probe_json(cmd):
        out = json.loads(probe.succeed(f"nixos-probe {cmd}"))
        assert "error" not in out, out
        return out

    start_all()
    probe.wait_for_unit("multi-user.target")
    dut.wait_for_unit("multi-user.target")

    with subtest("slot up"):
        up = probe_json("up --lan-vlan ${toString lanVlan}")
        assert up["slot"] == ${toString lanVlan}, up
        assert up["lan"]["trunk"] == "eth1", up
        wan = probe_json("wan-up --slot ${toString lanVlan} --wan-vlan ${toString wanVlan}")
        assert wan["ipv6"]["pd_pool"] == "2001:db8:c9:100::/56", wan
        info = probe_json("info --slot ${toString lanVlan}")
        assert all(info["daemons"].values()), info
        # the namespaces only see their own VLAN
        probe.succeed("nixos-probe exec --slot ${toString lanVlan} lan -- ip link show eth0")
        assert "@eth1" not in probe.succeed("ip -o link"), "slot device left on the trunk"

    with subtest("DUT gets an address and a prefix"):
        dut.succeed("systemd-run --unit odhcp6c odhcp6c -s /etc/odhcp6c-script -P 0 -v wan")
        dut.wait_until_succeeds("grep -q '^PREFIXES=2001:db8:c9:1' /run/odhcp6c.env", timeout=60)
        env = dict(
            line.split("=", 1) for line in dut.succeed("cat /run/odhcp6c.env").splitlines()
        )
        prefix = ipaddress.ip_network(env["PREFIXES"].split(",")[0].strip())
        address = env["ADDRESSES"].split("/")[0].strip()
        assert prefix.prefixlen == 60, prefix
        assert prefix.subnet_of(ipaddress.ip_network("2001:db8:c9:100::/56")), prefix
        assert ipaddress.ip_address(address) in ipaddress.ip_network("2001:db8:c9::/64"), address

        leases = probe_json("leases --slot ${toString lanVlan}")["leases"]
        kinds = {l["type"]: l["address"] for l in leases}
        assert kinds.get("pd") == str(prefix), leases
        assert kinds.get("na") == address, leases

    with subtest("LAN client behind the DUT"):
        lan64 = next(prefix.subnets(new_prefix=64))
        dut.succeed(f"ip -6 addr add {lan64[1]}/64 dev lan")
        dut.succeed(
            "printf 'interface lan {\\n AdvSendAdvert on;\\n MinRtrAdvInterval 3;\\n MaxRtrAdvInterval 10;\\n"
            f" prefix {lan64} {{ }};\\n}};\\n' > /run/radvd.conf"
        )
        dut.succeed("systemd-run --unit radvd radvd --nodaemon -C /run/radvd.conf -m stderr")
        probe.wait_until_succeeds(
            "nixos-probe exec --slot ${toString lanVlan} lan -- "
            f"ip -6 addr show dev eth0 scope global | grep -q '{str(lan64.network_address)[:-1]}'",
            timeout=60,
        )

        # scapy in the LAN namespace: solicit an RA, read its prefix
        ra = probe.succeed(
            "nixos-probe exec --slot ${toString lanVlan} lan -- python3 -c '"
            "from scapy.all import *; "
            "conf.verb = 0; "
            "a = srp1(Ether(dst=\"33:33:00:00:00:02\")/IPv6(dst=\"ff02::2\")/ICMPv6ND_RS(), "
            "iface=\"eth0\", timeout=10, filter=\"icmp6 and ip6[40] == 134\"); "
            "print(a[ICMPv6NDOptPrefixInfo].prefix)'"
        ).strip()
        assert ra == str(lan64.network_address), ra

    with subtest("routed through the DUT"):
        probe.succeed(
            f"nixos-probe exec --slot ${toString lanVlan} wan -- ip -6 route replace {prefix} via {address} dev eth0"
        )
        probe.wait_until_succeeds(
            "nixos-probe exec --slot ${toString lanVlan} lan -- ping -6 -c 1 -W 2 2001:db8:c9::1",
            timeout=30,
        )

    with subtest("down removes everything"):
        probe_json("down --slot ${toString lanVlan}")
        assert "np-" not in probe.succeed("ip netns list")
        probe.fail("pgrep kea-dhcp6")
        probe.fail("pgrep radvd")
        assert probe_json("info")["slots"] == []
  '';
}
