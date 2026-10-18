# Install the local terminal application; no network service is created.
{ inputs, ... }:
{
  flake.nixosModules.default = { config, lib, pkgs, ... }:
    let cfg = config.programs.hermes-agent;
    in {
      options.programs.hermes-agent = {
        enable = lib.mkEnableOption "Hermes local terminal application";
        package = lib.mkOption {
          type = lib.types.package;
          default = inputs.self.packages.${pkgs.stdenv.hostPlatform.system}.default;
          description = "Hermes CLI and TUI package.";
        };
      };
      config = lib.mkIf cfg.enable {
        environment.systemPackages = [ cfg.package ];
      };
    };
}
