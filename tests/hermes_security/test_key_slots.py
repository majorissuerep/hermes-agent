"""X25519 key slots: the vault holds ONLY public material on disk.

Contract (fork threat model — the system never holds the unlock credential):
- generate_keypair: raw 32-byte X25519 pair; private half shown once, human-stored.
- add_key_slot seals the master key to the PUBLIC half (ephemeral X25519 +
  HKDF + AES-GCM). Metadata contains no private material.
- unlock_with_private_key derives the SAME master key as the passphrase slot.
- Wrong key fails closed; rotation = add new + remove old; removed slot dead.
- v1 passphrase vaults keep working (slots are additive metadata).
- get_vault auto-unlocks via HERMES_VAULT_PRIVATE_KEY (path) like the
  password env var.
"""

import json

import pytest

from hermes_security import vault as hv


@pytest.fixture
def vault_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    hv.init_vault(home, "passphrase-1234", _allow_existing_state=True)
    hv.clear_vault_cache()
    return home


def test_keypair_shape():
    priv, pub = hv.generate_keypair()
    assert len(priv) == 32 and len(pub) == 32
    assert priv != pub


def test_slot_unlock_derives_same_master_key(vault_home):
    v1 = hv.unlock(vault_home, "passphrase-1234")
    priv, pub = hv.generate_keypair()
    hv.add_key_slot(vault_home, public_key_raw=pub, unlocked_vault=v1)
    hv.clear_vault_cache()
    v2 = hv.unlock_with_private_key(vault_home, priv)
    assert v2.master_key == v1.master_key


def test_metadata_holds_no_private_material(vault_home):
    v1 = hv.unlock(vault_home, "passphrase-1234")
    priv, pub = hv.generate_keypair()
    hv.add_key_slot(vault_home, public_key_raw=pub, unlocked_vault=v1)
    blob = json.dumps(json.loads((vault_home / ".hermes-vault").read_text()))
    assert priv.hex() not in blob
    import base64

    assert base64.urlsafe_b64encode(priv).decode() not in blob


def test_wrong_key_refused(vault_home):
    v1 = hv.unlock(vault_home, "passphrase-1234")
    _, pub = hv.generate_keypair()
    hv.add_key_slot(vault_home, public_key_raw=pub, unlocked_vault=v1)
    hv.clear_vault_cache()
    wrong, _ = hv.generate_keypair()
    with pytest.raises(hv.WrongMasterPasswordError):
        hv.unlock_with_private_key(vault_home, wrong)


def test_rotation_kills_old_slot(vault_home):
    v1 = hv.unlock(vault_home, "passphrase-1234")
    priv_old, pub_old = hv.generate_keypair()
    priv_new, pub_new = hv.generate_keypair()
    hv.add_key_slot(vault_home, public_key_raw=pub_old, unlocked_vault=v1)
    hv.add_key_slot(vault_home, public_key_raw=pub_new, unlocked_vault=v1)
    hv.remove_key_slot(vault_home, index=0)  # old dies
    hv.clear_vault_cache()
    v2 = hv.unlock_with_private_key(vault_home, priv_new)
    assert v2.master_key == v1.master_key
    hv.clear_vault_cache()
    with pytest.raises(hv.WrongMasterPasswordError):
        hv.unlock_with_private_key(vault_home, priv_old)


def test_passphrase_slot_survives_key_slots(vault_home):
    """v1 behavior untouched: passphrase still unlocks after slots added."""
    v1 = hv.unlock(vault_home, "passphrase-1234")
    _, pub = hv.generate_keypair()
    hv.add_key_slot(vault_home, public_key_raw=pub, unlocked_vault=v1)
    hv.clear_vault_cache()
    assert hv.unlock(vault_home, "passphrase-1234").master_key == v1.master_key


def test_no_slot_vault_rejects_key_unlock(vault_home):
    hv.clear_vault_cache()
    priv, _ = hv.generate_keypair()
    with pytest.raises(hv.VaultLockedError):
        hv.unlock_with_private_key(vault_home, priv)


def test_env_private_key_auto_unlock(vault_home, monkeypatch):
    v1 = hv.unlock(vault_home, "passphrase-1234")
    priv, pub = hv.generate_keypair()
    hv.add_key_slot(vault_home, public_key_raw=pub, unlocked_vault=v1)
    key_file = vault_home.parent / "priv.key"
    import base64

    key_file.write_text(base64.urlsafe_b64encode(priv).decode().rstrip("="))
    hv.clear_vault_cache()
    monkeypatch.setenv("HERMES_VAULT_PRIVATE_KEY", str(key_file))
    got = hv.get_vault(vault_home)
    assert got.master_key == v1.master_key


def test_seal_is_nondeterministic(vault_home):
    """Two seals of the same key to the same public key differ (ephemeral)."""
    v1 = hv.unlock(vault_home, "passphrase-1234")
    _, pub = hv.generate_keypair()
    hv.add_key_slot(vault_home, public_key_raw=pub, unlocked_vault=v1)
    hv.add_key_slot(vault_home, public_key_raw=pub, unlocked_vault=v1)
    slots = json.loads((vault_home / ".hermes-vault").read_text())["key_slots"]
    assert slots[0]["sealed"] != slots[1]["sealed"]
    assert slots[0]["eph"] != slots[1]["eph"]
