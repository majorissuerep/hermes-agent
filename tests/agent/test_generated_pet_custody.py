"""Generated pets retain private media through rendering, rename and explicit export."""
from __future__ import annotations

import io
import json
import secrets
import zipfile

import pytest
from PIL import Image

from agent.pet import render, store
from hermes_security import vault
from hermes_security.errors import VaultError


@pytest.fixture
def home(tmp_path, monkeypatch):
    selected = tmp_path / "owner"
    selected.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    vault.init_vault(selected, "synthetic generated pet password")
    vault.unlock(selected, "synthetic generated pet password")
    yield selected
    vault.clear_vault_cache()


def test_generated_pet_keeps_private_identity_pixels_and_native_consumers(home):
    canary = secrets.token_hex(24)
    source = Image.new("RGBA", (4, 18), (23, 91, 157, 255))
    pet = store.register_local_pet(source, slug="private-pet", display_name="Private animal", description=canary)
    assert pet.spritesheet.read_bytes().startswith(b"HRMVAULT\x00")
    assert all(canary.encode() not in p.read_bytes() for p in home.rglob("*") if p.is_file())
    assert store.load_pet(pet.slug).generated
    renderer = render.PetRenderer(pet.spritesheet, mode="unicode", frame_w=2, frame_h=2)
    assert renderer.frame_count("idle") == 2
    assert store.thumbnail_png(pet.slug).startswith(b"\x89PNG\r\n\x1a\n")
    original_path = pet.spritesheet
    new_slug = store.rename_pet(pet.slug, "Renamed animal")
    assert new_slug == "renamed-animal" and store.load_pet(pet.slug) is None
    renamed = store.load_pet(new_slug)
    assert renamed.spritesheet == original_path and renamed.description == canary
    filename, exported = store.export_pet(new_slug)
    assert filename == "renamed-animal.zip"
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        metadata = json.loads(archive.read(new_slug + "/pet.json"))
        assert metadata["description"] == canary and metadata["displayName"] == "Renamed animal"
        assert archive.read(new_slug + "/" + metadata["spritesheetPath"]).startswith(b"RIFF")
    assert all(canary.encode() not in p.read_bytes() for p in home.rglob("*") if p.is_file())
    vault.lock_now(home)
    with pytest.raises(VaultError):
        renderer.frame_count("idle")
    vault.unlock(home, "synthetic generated pet password")
    with pytest.raises(VaultError):
        renderer.frame_count("idle")
    fresh = render.PetRenderer(renamed.spritesheet, mode="unicode", frame_w=2, frame_h=2)
    assert fresh.frame_count("idle") == 2


def test_damaged_generated_registry_never_becomes_an_empty_public_catalog(home):
    from agent.pet import private_media

    pet = store.register_local_pet(Image.new("RGBA", (4, 18), "blue"), slug="kept")
    registry = home / private_media.REGISTRY
    before = pet.spritesheet.read_bytes()
    damaged = bytearray(registry.read_bytes())
    damaged[-1] ^= 1
    registry.write_bytes(damaged)
    with pytest.raises(VaultError):
        store.installed_pets()
    assert pet.spritesheet.read_bytes() == before


def test_payload_cache_and_foreign_profile_never_borrow_retained_private_pixels(home, monkeypatch):
    from tui_runtime.pet_media import sprite_payload

    pet = store.register_local_pet(Image.new("RGBA", (4, 18), "navy"), slug="same-id")
    first = sprite_payload(pet, scale=1)
    assert first["spritesheetBase64"] and sprite_payload(pet, scale=1) == first
    vault.lock_now(home)
    vault.unlock(home, "synthetic generated pet password")
    with pytest.raises(VaultError):
        sprite_payload(pet, scale=1)
    fresh = store.load_pet("same-id")
    assert sprite_payload(fresh, scale=1) == first
    other = home.parent / "other"
    other.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(other))
    vault.init_vault(other, "synthetic second pet password")
    vault.unlock(other, "synthetic second pet password")
    foreign = store.register_local_pet(Image.new("RGBA", (4, 18), "green"), slug="same-id")
    assert sprite_payload(foreign, scale=1)["spritesheetBase64"] != first["spritesheetBase64"]
    with pytest.raises(PermissionError, match="different profile"):
        sprite_payload(fresh, scale=1)


def _legacy_pet(home):
    home.mkdir(exist_ok=True)
    directory = home / "pets/legacy-animal"
    directory.mkdir(parents=True)
    original = io.BytesIO()
    Image.new("RGBA", (4, 18), "navy").save(original, format="WEBP", lossless=True)
    canary = secrets.token_hex(24)
    metadata = {"id": "legacy-animal", "displayName": "Legacy animal", "description": canary,
                "createdBy": "generator", "spritesheetPath": "spritesheet.webp",
                "future_metadata": {"private_note": canary}}
    (directory / "pet.json").write_text(json.dumps(metadata))
    (directory / "spritesheet.webp").write_bytes(original.getvalue())
    public = home / "cache/pet-catalog/catalog-pet"
    public.mkdir(parents=True)
    (public / "pet.json").write_text('{"id":"catalog-pet","displayName":"Public catalog pet"}')
    (public / "spritesheet.webp").write_bytes(original.getvalue())
    return directory, original.getvalue(), metadata


def test_explicit_repair_preserves_legacy_pet_identity_bytes_metadata_and_public_assets(home):
    from hermes_security.migrate import repair_clobbered_state, scan_for_plaintext
    from agent.pet import private_media

    legacy, original, metadata = _legacy_pet(home)
    public = {p.name: p.read_bytes() for p in (home / "cache/pet-catalog/catalog-pet").iterdir()}
    assert "pets/legacy-animal/pet.json" in scan_for_plaintext(home)
    with pytest.raises(store.PetStoreError, match="repair"):
        store.load_pet("legacy-animal")
    report = repair_clobbered_state(home)
    assert report["unrecoverable"] == [] and not legacy.exists()
    pet = store.load_pet("legacy-animal")
    assert pet.description == metadata["description"] and private_media.read_image(pet.spritesheet) == original
    assert scan_for_plaintext(home) == []
    sealed = {p: p.read_bytes() for p in (home / private_media.ROOT).rglob("*") if p.is_file()}
    assert repair_clobbered_state(home)["unrecoverable"] == []
    assert {p: p.read_bytes() for p in sealed} == sealed
    assert {p.name: p.read_bytes() for p in (home / "cache/pet-catalog/catalog-pet").iterdir()} == public
    with zipfile.ZipFile(io.BytesIO(store.export_pet(pet.slug)[1])) as archive:
        assert json.loads(archive.read(pet.slug + "/pet.json"))["future_metadata"] == metadata["future_metadata"]
        assert archive.read(pet.slug + "/spritesheet.webp") == original


def test_legacy_repair_refuses_unknown_files_without_deleting_the_only_copy(home):
    from hermes_security.migrate import repair_clobbered_state

    legacy, _, _ = _legacy_pet(home)
    (legacy / "unknown-user-data.txt").write_text(secrets.token_hex(24))
    orphan = home / "pets/.thumbs/orphan.png"
    orphan.parent.mkdir()
    orphan.write_bytes(secrets.token_bytes(24))
    before = {p: p.read_bytes() for p in [*legacy.iterdir(), orphan]}
    report = repair_clobbered_state(home)
    assert any("pets/.thumbs/orphan.png" in message for message in report["unrecoverable"])
    assert report["unrecoverable"]
    assert {p: p.read_bytes() for p in before} == before


def test_first_migration_includes_generated_pets_even_when_public_tree_was_previously_skipped(tmp_path, monkeypatch):
    from hermes_security.migrate import migrate_home, scan_for_plaintext

    selected = tmp_path / "pre-vault"
    legacy, original, metadata = _legacy_pet(selected)
    monkeypatch.setenv("HERMES_HOME", str(selected))
    monkeypatch.setattr(vault, "_SCRYPT_N", 1024)
    try:
        result = migrate_home(selected, "synthetic initial pet migration password")
        assert result.ok and result.backup_path is not None
        assert not legacy.exists() and scan_for_plaintext(selected) == []
        pet = store.load_pet("legacy-animal")
        assert pet.description == metadata["description"]
        from agent.pet.private_media import read_image

        assert read_image(pet.spritesheet) == original
    finally:
        vault.clear_vault_cache()


def test_catalog_rename_keeps_private_labels_out_of_public_assets_and_reinstall_is_independent(home, monkeypatch):
    from types import SimpleNamespace

    encoded = io.BytesIO()
    Image.new("RGBA", (4, 18), "navy").save(encoded, format="WEBP", lossless=True)
    original = encoded.getvalue()
    public_metadata = {"id": "catalog-boba", "displayName": "Public Boba", "spritesheetPath": "spritesheet.webp",
                       "license": {"public": "CC0"}}
    entry = SimpleNamespace(slug="catalog-boba", display_name="Public Boba",
                            spritesheet_url="https://assets.petdex.dev/catalog-boba.webp",
                            pet_json_url="https://assets.petdex.dev/catalog-boba.json")
    monkeypatch.setattr("agent.pet.manifest.find_entry", lambda *_args, **_kwargs: entry)
    monkeypatch.setattr(store, "_download", lambda _url, path, **_kwargs: path.write_bytes(original))
    monkeypatch.setattr(store, "_http_get", lambda *_args: SimpleNamespace(json=lambda: dict(public_metadata)))
    installed = store.install_pet("catalog-boba")
    canary = secrets.token_hex(24)
    label = "Private label " + canary
    renamed = store.rename_pet(installed.slug, label)
    assert renamed != installed.slug and store.load_pet(installed.slug) is None
    assert all(canary.encode() not in p.read_bytes() and canary not in str(p)
               for p in home.rglob("*") if p.is_file())
    assert store.load_pet(renamed).spritesheet == installed.spritesheet
    assert store.resolve_active_pet(renamed).slug == renamed
    source = home / "cache/pet-catalog/catalog-boba"
    assert json.loads((source / "pet.json").read_text())["displayName"] == "Public Boba"
    assert (source / "spritesheet.webp").read_bytes() == original
    reinstalled = store.install_pet("catalog-boba")
    assert reinstalled.spritesheet != installed.spritesheet
    assert {p.slug for p in store.installed_pets()} == {"catalog-boba", renamed}
    with zipfile.ZipFile(io.BytesIO(store.export_pet(renamed)[1])) as archive:
        metadata = json.loads(archive.read(renamed + "/pet.json"))
        assert metadata["displayName"] == label and metadata["license"] == public_metadata["license"]
        assert archive.read(renamed + "/spritesheet.webp") == original
    assert store.remove_pet("catalog-boba") and store.load_pet(renamed) is not None
    assert (source / "spritesheet.webp").read_bytes() == original


def test_explicit_repair_encrypts_unmarked_current_legacy_identity_without_guessing_catalog_origin(home):
    from hermes_security.migrate import repair_clobbered_state, scan_for_plaintext

    directory = home / "pets/UserPet.ID"
    directory.mkdir(parents=True)
    source = io.BytesIO()
    Image.new("RGBA", (4, 18), "blue").save(source, format="WEBP", lossless=True)
    canary = secrets.token_hex(24)
    (directory / "spritesheet.webp").write_bytes(source.getvalue())
    (directory / "pet.json").write_text(json.dumps({"id": "UserPet.ID", "displayName": canary,
                                                    "spritesheetPath": "spritesheet.webp"}))
    assert "pets/UserPet.ID/pet.json" in scan_for_plaintext(home)
    assert repair_clobbered_state(home)["unrecoverable"] == []
    pet = store.load_pet("UserPet.ID")
    assert pet.slug == "UserPet.ID" and pet.display_name == canary and not pet.generated
    assert not directory.exists() and scan_for_plaintext(home) == []
    assert all(canary.encode() not in p.read_bytes() for p in home.rglob("*") if p.is_file())
