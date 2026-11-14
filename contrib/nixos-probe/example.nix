# Example probe: one port (management untagged, the DUTs' VLANs tagged).
# Replace the boot and file system settings with the machine's own
# (hardware-configuration.nix).
{ lib, ... }:
{
  networking.hostName = "nixos-probe";

  services.nixos-probe = {
    enable = true;
    # trunk = "enp1s0";  # default: the port the default route uses
    authorizedKeys = [
      # "ssh-ed25519 AAAA... labgrid@labgrid-<lab>"
    ];
  };

  # Management address: the one in the lab's exporter.yaml
  networking.useDHCP = lib.mkDefault true;

  boot.loader.grub.device = lib.mkDefault "nodev";
  fileSystems."/" = lib.mkDefault {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
  };

  system.stateVersion = "26.05";
}
