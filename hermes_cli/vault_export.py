"""CLI boundary for explicitly requested plaintext recovery exports."""

from __future__ import annotations


def export_command(args) -> int:
    from hermes_constants import get_hermes_home
    from hermes_cli.vault_cmd import gate_locked_vault
    from hermes_security.errors import VaultError
    from hermes_security.export import export_home

    if not args.output:
        print("secure-vault export requires --output /outside/home/recovery.tar")
        return 2
    home = get_hermes_home()
    gate_locked_vault(args, home)
    try:
        report = export_home(home, args.output)
    except (OSError, ValueError, VaultError) as exc:
        print(f"Export failed; encrypted originals retained: {exc}")
        return 1
    print(f"Exported {report['files']} files ({report['databases']} databases) to {args.output}")
    print("This recovery archive is PLAINTEXT. Keep it private; the original home remains encrypted.")
    return 0
