"""``hermes secure-vault`` subcommand: master-password at-rest encryption."""

from __future__ import annotations


def build_secure_vault_parser(subparsers) -> None:
    """Attach the fork's master-password vault subcommand.
    Deferred handler import: registering the parser must not load the crypto
    stack (cryptography must stay out of update dispatch)."""

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
        choices=["init", "migrate", "unlock", "status", "lock", "keygen", "add-key", "remove-key", "slots"],
        help="init = create vault on a fresh home; migrate = convert an existing plaintext home "
        "in place (full tar backup first, every file verified, sessions/config preserved); "
        "unlock = unlock for this process; status = show state; lock = drop this process's keys; "
        "keygen = generate an X25519 keypair (private key shown ONCE, store it yourself — "
        "the system never holds it); add-key = seal the vault to a public key (key slot); "
        "remove-key = drop a key slot (rotation); slots = list key slots",
    )
    parser.add_argument("--yes", action="store_true", help="migrate: skip the confirmation prompt")
    parser.add_argument(
        "--public-key", default=None,
        help="add-key: path to a raw 32-byte public key file (base64 or hex)",
    )
    parser.add_argument(
        "--private-key", default=None,
        help="unlock: path to a raw 32-byte private key file (base64 or hex). "
        "Env: HERMES_VAULT_PRIVATE_KEY (path)",
    )
    parser.add_argument(
        "--slot", type=int, default=None,
        help="remove-key: slot index to drop (see 'slots')",
    )

    def _run(args):
        from hermes_cli.vault_cmd import cmd_vault

        return cmd_vault(args)

    parser.set_defaults(func=_run)
