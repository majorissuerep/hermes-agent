"""Authenticated profile-file copies; encrypted bytes never retain another path's AAD."""
from pathlib import Path
import shutil


def copy_profile_file(source: Path, destination: Path, *, purpose: str) -> None:
    from hermes_security import io
    from hermes_security.errors import PlaintextStateError

    destination.parent.mkdir(parents=True, exist_ok=True)
    source_owner, destination_owner = io._home_for(source), io._home_for(destination)
    if source_owner is None and destination_owner is None:
        # Pre-vault profile bootstrap remains supported; no encrypted credential is downgraded.
        shutil.copy2(source, destination)
        return
    if destination_owner is None:
        raise PlaintextStateError("Initialize the destination profile vault before copying encrypted state")
    content = io.read_bytes(source, purpose=purpose)
    if content is None:
        raise FileNotFoundError(source)
    io.write_bytes(destination, content, purpose=purpose)


def _purpose(path: Path) -> str:
    from hermes_security.migrate import _purpose_for

    if path.parts[:2] == ("backups", "config"):
        return "config"  # config_backups retains the config writer's purpose label.
    return _purpose_for(path)


def _database(path: Path) -> bool:
    return path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}


def _copy_database(source: Path, destination: Path, *, final_path: Path | None = None) -> None:
    from contextlib import closing
    from hermes_security import sqlite

    with closing(sqlite.connect(f"file:{source}?mode=ro", uri=True)) as donor:
        with closing(sqlite.connect(destination, key_path=final_path or destination)) as recipient:
            donor.backup(recipient)
            recipient.commit()
    destination.chmod(0o600)


def _public_asset(relative: Path) -> bool:
    from hermes_security.migrate import _SKIP_DIRS

    return any(part in _SKIP_DIRS for part in relative.parts[:-1])


def copy_profile_tree_file(source: str, destination: str, *, source_root: Path) -> str:
    """copytree callback: code assets stay code; private state is authenticated and rebound."""
    from hermes_security import io, frames, vault
    from hermes_security.migrate import _frame_purpose
    from hermes_security.errors import VaultIntegrityError

    src, dst = Path(source), Path(destination)
    relative = src.relative_to(source_root)
    owner = io._home_for(src)
    if owner is None or _public_asset(relative):
        shutil.copy2(src, dst)
        return str(dst)
    with src.open("rb") as handle:
        envelope = handle.read(9) == b"HRMVAULT\x00"
    if envelope:
        copy_profile_file(src, dst, purpose=_purpose(relative))
    elif _database(src):
        _copy_database(src, dst)
    elif src.suffix in {".log", ".jsonl"}:
        raw = src.read_bytes()
        records, consumed = frames.split_stream(
            raw, vault=vault.get_vault(owner), purpose=_frame_purpose(src), strict=True)
        if consumed != len(raw):
            raise VaultIntegrityError(f"Incomplete profile stream: {relative}")
        target_owner = io._home_for(dst)
        if target_owner is None:
            raise VaultIntegrityError("Profile stream destination has no vault")
        vault._atomic_write(dst, frames.encode_frames(
            records, vault=vault.get_vault(target_owner), purpose=_frame_purpose(src)))
    else:
        # Never promote unknown plaintext state from a vaulted donor into a valid clone.
        copy_profile_file(src, dst, purpose=_purpose(relative))
    return str(dst)


def publish_profile_state(staging: Path, destination: Path) -> None:
    """Bind a hidden profile's authenticated state to its final path before atomic rename.

    A failure leaves only unpublished staging bytes for the caller to discard. Donor files and
    existing profiles are never rewritten. SQLCipher pages remain encrypted during rekeying.
    """
    import os
    import tempfile
    from hermes_security import io, vault

    owner = io._home_for(staging)
    if owner is None:
        return  # Legacy pre-vault layout; the public command still owns its normal vault gate.
    final_owner = io._home_for(destination)
    if final_owner is None:
        raise ValueError("Published profile must remain inside a vault")
    final_vault = vault.get_vault(final_owner)
    for path in sorted(staging.rglob("*")):
        relative = path.relative_to(staging)
        if path.is_symlink() or not path.is_file() or _public_asset(relative):
            continue
        target = destination / relative
        with path.open("rb") as handle:
            envelope = handle.read(9) == b"HRMVAULT\x00"
        if envelope:
            purpose = _purpose(relative)
            content = io.read_bytes(path, purpose=purpose)
            sealed = final_vault.encrypt(content, purpose=purpose,
                                         relpath=target.resolve(strict=False).relative_to(final_owner).as_posix())
            vault._atomic_write(path, sealed)
        elif _database(path):
            fd, name = tempfile.mkstemp(prefix=".profile-publish-", dir=path.parent)
            os.close(fd)
            temporary = Path(name)
            try:
                _copy_database(path, temporary, final_path=target)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)


def profile_clone_exclusions(directory: Path, names: list[str], *, source_root: Path) -> set[str]:
    """Vault authority and live synchronization sidecars never belong to a new profile."""
    from hermes_cli.home_data_layout import PM_RUNTIME_ROOT_DIRS

    excluded = {name for name in names if name == ".hermes-vault" or name.startswith(".hermes-vault.")}
    if directory == source_root:
        excluded.update(PM_RUNTIME_ROOT_DIRS & set(names))
    for name in names:
        relative = (directory / name).relative_to(source_root)
        if _public_asset(relative):
            continue
        if (name in {".env.lock", "config.yaml.lock", "runtime.lock", "gateway.lock", ".pm-write.lock"}
                or name.endswith((".log.lock", ".jsonl.lock"))):
            excluded.add(name)
        elif any(name.endswith(suffix) and _database(Path(name.removesuffix(suffix)))
                 for suffix in ("-wal", "-shm", ".quarantine.lock", ".repair.lock",
                                ".auto-maintenance.lock", ".fts_rebuild.lock")):
            excluded.add(name)  # SQLCipher snapshots WAL data; lock identities do not transfer.
    return excluded


def rename_profile_state(source: Path, destination: Path) -> None:
    """Publish a rekeyed sibling atomically; retain the donor until publication succeeds."""
    import logging
    import os
    import secrets
    from functools import partial
    from hermes_cli.profiles import (
        _clone_staging_dir, _copytree_keep_junctions, _materialize_symlinked_files,
    )
    from hermes_security import io, vault

    source_owner = io._home_for(source)
    if source_owner is None:
        source.rename(destination)
        return
    staging = _clone_staging_dir(destination)
    rollback = source.parent / f".{source.name}.renaming-{secrets.token_hex(8)}"
    moved = False
    try:
        _copytree_keep_junctions(
            source, staging,
            lambda directory, names: profile_clone_exclusions(Path(directory), names, source_root=source),
            copy_function=partial(copy_profile_tree_file, source_root=source),
        )
        _materialize_symlinked_files(staging)
        publish_profile_state(staging, destination)
        source.rename(rollback)
        moved = True
        try:
            os.rename(staging, destination)
        except BaseException:
            rollback.rename(source)
            moved = False
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    if moved:
        try:
            shutil.rmtree(rollback)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Profile renamed; original encrypted snapshot retained at %s: %s", rollback, exc)
        if source_owner == source.resolve():
            vault.lock_now(source_owner)
