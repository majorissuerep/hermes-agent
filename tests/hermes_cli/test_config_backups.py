"""config.yaml backups: one dir, deduped, bounded — never a pile of siblings in HERMES_HOME."""
from pathlib import Path

from hermes_cli.config_backups import backup_config, list_config_backups


def test_repeat_backups_dedupe_and_rotate(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model: a\n")
    stamps = iter(f"2026010100000{i}" for i in range(10))
    monkeypatch.setattr("hermes_cli.config_backups.time.strftime", lambda _fmt: next(stamps))

    first = backup_config(cfg, "pre-setup", keep=2)
    assert first is not None and first.parent == tmp_path / "backups" / "config"
    # Same bytes again → no new file (the hermes-setup-three-times case).
    assert backup_config(cfg, "pre-setup", keep=2) is None
    for i in range(3):
        cfg.write_text(f"model: {i}\n")
        backup_config(cfg, "pre-setup", keep=2)
    kept = list_config_backups(cfg, "pre-setup")
    assert len(kept) == 2 and kept[0].read_text() == "model: 2\n"
    # Nothing left beside config.yaml in the home root.
    assert [p.name for p in tmp_path.iterdir() if p.is_file()] == ["config.yaml"]


def test_legacy_siblings_move_but_user_named_copies_stay(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model: a\n")
    for name in ("config.yaml.bak.1778718391", "config.yaml.corrupt.20260729-093706.bak",
                 "config.yaml.bak-pre-migrate-xai-20260515-120000"):
        (tmp_path / name).write_text("old")
    (tmp_path / "config.yaml.bak-my-note").write_text("mine")

    backup_config(cfg, "corrupt")

    root = tmp_path / "backups" / "config"
    assert (root / "config.yaml.bak.1778718391").exists()
    assert (root / "config.yaml.corrupt.20260729-093706.bak").exists()
    assert (tmp_path / "config.yaml.bak-my-note").read_text() == "mine"
    assert not list(tmp_path.glob("config.yaml.bak.*")) and not list(tmp_path.glob("config.yaml.corrupt.*"))


def test_sealed_backup_reads_back_and_dedupes_on_plaintext(tmp_path: Path, monkeypatch):
    """Fork: an envelope binds its path into the AAD, so a byte copy is undecryptable at the backup's
    path — the last-known-good safety net silently never worked in a vaulted home."""
    from hermes_cli.config_backups import load_newest_good_backup
    from hermes_security import migrate as mig
    from hermes_security import vault as hv

    home = tmp_path / ".hermes"
    home.mkdir()
    cfg = home / "config.yaml"
    cfg.write_text("model: sealed\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert mig.migrate_home(home, "backup-password").ok
    hv.clear_vault_cache()
    hv.unlock(home, "backup-password")

    assert backup_config(cfg, "good") is not None
    assert backup_config(cfg, "good") is None  # same plaintext, fresh nonce: still a duplicate
    assert load_newest_good_backup(cfg) == {"model": "sealed"}
