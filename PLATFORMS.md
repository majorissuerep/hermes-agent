# Fork Platform Policy

**Native Windows is NOT supported. Supported platforms: Linux, macOS, and Windows via WSL.**

This is a deliberate fork policy (see `pyproject.toml` classifiers: POSIX/Linux + MacOS only).

## What was removed

- Win32-only dependencies (`pywinpty`, `pywin32`, `winrt-*`, `tzdata; win32`, `concurrent-log-handler; win32`, `distlib; win32`).
- Windows installers and update scripts (`scripts/install.ps1`, `setup-hermes.ps1`, `scripts/desktop-update/*.ps1`, `scripts/build*ps1`).
- Windows CI lanes (`.github/workflows/windows-*.yml`, `install-e2e-windows-run.yml`, Windows matrix rows in `tests-os.yml`/`pm-bundle.yml`/`stable-release.yml`/`desktop-bundled-release.yml`/`install-e2e.yml`/`bootstrap-installer*.yml`, the `windows-footguns` lint job).
- Windows service backends: `hermes_cli/gateway_windows.py`, `gateway_windows_legacy.py`, `win_pty_bridge.py`, `windows_ssh_runtime.py`, `windows_store_update.py`, `windows_appinstaller_update.py`.
- `hermes_cli/update_cmd_windows.py` was split: cross-platform venv-holder/argv parsing survives in `hermes_cli/update_cmd_holders.py`; Windows SCM/schtasks lifecycle functions are documented no-ops.
- Windows registry/WMI probes in `hermes_platform/host/facts.py` (POSIX/macOS detection paths kept).
- Desktop app: Windows remote lifecycle entry points, MSIX/AppInstaller/Store update strategies, `win:`/`msix:` builder targets.

## Rules going forward

1. Do not reintroduce `sys_platform == 'win32'` dependencies or Windows-only modules.
2. When merging upstream, drop Windows-only hunks/modules rather than resolving them.
3. WSL is the answer for Windows hardware: Hermes runs inside the WSL Linux distro.
4. Residual inline `sys.platform == "win32"` ternaries are dead code on supported platforms and
   will be removed opportunistically; do not add new ones.
