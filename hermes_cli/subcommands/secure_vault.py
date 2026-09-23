"""``hermes secure-vault`` subcommand: master-password at-rest encryption."""

from __future__ import annotations


def build_secure_vault_parser(subparsers) -> None:
    """Attach the fork's master-password vault subcommand."""
    from hermes_cli.vault_cmd import cmd_vault

    parser = subparsers.add_parser(
        "secure-vault",
        help="Master-password encryption of all Hermes state (init/unlock/status/lock)",
        description=(
            "Mandatory at-rest encryption: every config file, credential store, "
            "database, transcript and log in the Hermes home is encrypted with "
            "keys derived from your master password. Lose the password and the "
            "data is gone - there is no recovery by design."
        ),
    )
    parser.add_argument(
        "vault_command",
        nargs="?",
        default="status",
        choices=["init", "migrate", "unlock", "status", "lock"],
        help="init = create vault on a fresh home; migrate = convert an existing plaintext home "
        "in place (full tar backup first, every file verified, sessions/config preserved); "
        "unlock = unlock for this process; status = show state; lock = drop this process's keys",
    )
    parser.add_argument("--yes", action="store_true", help="migrate: skip the confirmation prompt")
    parser.set_defaults(func=cmd_vault)
