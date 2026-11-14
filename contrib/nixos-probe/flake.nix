{
  description = "NixOS probe for openwrt-tests: DHCPv6-PD upstream, scapy, raw L2";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = nixpkgs.lib.genAttrs systems;
    in
    {
      nixosModules.default = ./module.nix;

      # A starting point: copy example.nix next to the machine's
      # hardware-configuration.nix, or import the module from your own flake
      nixosConfigurations = forAll (
        system:
        nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [
            self.nixosModules.default
            ./example.nix
          ];
        }
      );

      # Probe and DUT VMs: slot, DHCPv6 with PD, SLAAC behind the DUT,
      # routing through it, scapy in a namespace
      checks = forAll (system: {
        vm = nixpkgs.legacyPackages.${system}.testers.runNixOSTest (import ./test.nix self);
      });
    };
}
