{ writeShellApplication, bash, coreutils, git }:
writeShellApplication {
  name = "sandbox";
  runtimeInputs = [ bash coreutils git ];
  text = ''
    exec ${../scripts/dev-sandbox.sh} "$@"
  '';
}
