# NixOS module: the nixos-probe for openwrt-tests (tests/nixos_probe_fixtures.py).
#
# Tests reach the probe over SSH as root (labgrid SSHDriver, the place's
# NetworkService) and run `nixos-probe` and their own tools in per-DUT
# network namespaces.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.nixos-probe;

  python = pkgs.python3.withPackages (p: [ p.scapy ]);

  # Tools the tests run in a slot's namespaces
  tools = with pkgs; [
    iproute2
    procps # sysctl
    kea
    radvd
    dhcpcd
    odhcp6c
    ndisc6
    tcpdump
    iperf3
    iputils
    ethtool
    jq
    python
  ];

  nixos-probe = pkgs.runCommand "nixos-probe" { nativeBuildInputs = [ pkgs.makeWrapper ]; } ''
    install -Dm755 ${./nixos-probe.py} $out/libexec/nixos-probe.py
    makeWrapper ${python}/bin/python3 $out/bin/nixos-probe \
      --add-flags "-I $out/libexec/nixos-probe.py" \
      --prefix PATH : ${lib.makeBinPath tools}
  '';
in
{
  options.services.nixos-probe = {
    enable = lib.mkEnableOption "the openwrt-tests NixOS probe";

    trunk = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "enp1s0";
      description = ''
        The port that carries the DUTs' LAN and WAN VLANs tagged. Null: the
        port the default route uses, i.e. the management port.
      '';
    };

    parents = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [
        "eth1"
        "vlan101"
      ];
      description = ''
        Interfaces a slot may sit on without a VLAN (a private macvlan), for a
        lab host that is the probe itself and already has the DUT
        interfaces. Slots on any other interface are refused.
      '';
    };

    authorizedKeys = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = ''
        SSH keys that may log in as root: those of the user running the
        tests (the KernelCI agent, the lab's coordinator host).
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    environment.etc."nixos-probe.json".text = builtins.toJSON {
      inherit (cfg) trunk parents;
    };

    environment.systemPackages = [ nixos-probe ] ++ tools;

    boot.kernelModules = [
      "8021q"
      "macvlan"
    ];

    services.openssh = {
      enable = true;
      settings = {
        PermitRootLogin = "prohibit-password";
        PasswordAuthentication = false;
      };
    };
    users.users.root.openssh.authorizedKeys.keys = cfg.authorizedKeys;

    # Slot devices are created in the root namespace and moved at once; keep
    # the host's network management off them meanwhile
    networking.dhcpcd.denyInterfaces = [ "np*" ];
    systemd.network.networks."05-nixos-probe" = lib.mkIf config.systemd.network.enable {
      matchConfig.Name = "np*";
      linkConfig.Unmanaged = true;
    };

    # Slots don't survive a reboot; drop their state
    systemd.tmpfiles.rules = [ "D /run/nixos-probe 0700 root root -" ];
  };
}
