# Pure configuration rendering used by terminal package checks.
{ lib }:
let
  effectivePackage =
    cfg:
    if cfg.extraPythonPackages == [ ] && cfg.extraDependencyGroups == [ ] then
      cfg.package
    else
      cfg.package.override { inherit (cfg) extraPythonPackages extraDependencyGroups; };

  # ── The rendered config.yaml ────────────────────────────────────────────
  # YAML contains JSON, so the output of toJSON is a correct config.yaml.
  # terminal.cwd replaces the old MESSAGING_CWD environment variable. The
  # order of the recursiveUpdate lets an explicit settings.terminal.cwd
  # replace the default value.
  #
  # The file also carries the `_config_version` of the package. In managed
  # mode Hermes refuses to write config.yaml, so it cannot stamp the version
  # itself, and an unstamped file reads as version 0 at every boot. The build
  # reads the version from DEFAULT_CONFIG so it always matches the package.
  mkConfigFiles =
    {
      pkgs,
      cfg,
      workingDirectory,
    }:
    let
      generated =
        pkgs.runCommand "hermes-config.yaml"
          {
            settings = builtins.toJSON (lib.recursiveUpdate { terminal.cwd = workingDirectory; } cfg.settings);
            passAsFile = [ "settings" ];
          }
          ''
            HOME=$TMPDIR ${(effectivePackage cfg).hermesVenv}/bin/python3 - "$settingsPath" > $out <<'PY'
            import json, sys
            from hermes_cli.config_defaults import DEFAULT_CONFIG
            with open(sys.argv[1]) as f:
                settings = json.load(f)
            settings.setdefault("_config_version", DEFAULT_CONFIG["_config_version"])
            json.dump(settings, sys.stdout)
            PY
          '';
    in
    {
      inherit generated;
      effective = if cfg.configFile != null then cfg.configFile else generated;
      mergeScript = pkgs.callPackage ./configMergeScript.nix { };
    };

in { inherit effectivePackage mkConfigFiles; }
