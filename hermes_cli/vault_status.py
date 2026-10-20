"""Credential-format-aware, read-only vault status presentation."""
import base64


def print_status(home) -> int:
    from hermes_cli.vault_cmd import vault_mod

    if not vault_mod.vault_exists(home):
        print(f"○ No vault at {home} (state writes will be refused; run 'hermes secure-vault migrate')")
        return 1
    status = vault_mod.vault_status(home)
    meta = status["meta"]
    print(f"● Vault present at {home}")
    print(f"  Integrity        : {status['integrity']}")
    print(f"  Unlocked in proc : {'yes' if status['unlocked'] else 'no'}")
    if meta:
        # Wrapped credentials use their own KDF salt; the root salt is only a master-key proof.
        credential = meta.get("passphrase", meta)
        credential = credential if isinstance(credential, dict) else {}
        kdf = credential.get("kdf", "unknown")
        n, r, p = (credential.get(key, "?") for key in ("kdf_n", "kdf_r", "kdf_p"))
        print(f"  KDF              : {kdf} (n={n}, r={r}, p={p})")
        print(f"  Version          : v{meta.get('version', '?')}")
        # This is public verification metadata, never a credential or wrapped master.
        if salt_b64 := meta.get("salt", ""):
            salt_hex = base64.urlsafe_b64decode(salt_b64).hex()
            print(f"  Salt             : {salt_hex} ({len(salt_b64)} b64 chars)")
    envelopes, dbs, frames = status["encrypted_files"]
    print(f"  Encrypted files  : {envelopes} envelope(s), {dbs} database(s), {frames} frame stream(s)")
    if not status["unlocked"]:
        print("  (use 'hermes secure-vault unlock', or HERMES_VAULT_PRIVATE_KEY=<key-file path>, to unlock)")
    return 0
