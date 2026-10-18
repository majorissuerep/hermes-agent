"""Existing vaulted profiles retire only the old seeded desktop image default."""

import secrets


def test_retired_default_is_unpinned_but_user_image_and_other_settings_survive(tmp_path, monkeypatch):
    import hermes_yaml as yaml
    from hermes_cli.config_migrations import run_migrations
    from hermes_security import io as state_io, vault

    home = tmp_path / "profile"
    password = secrets.token_urlsafe(24)
    vault.init_vault(home, password)
    vault.unlock(home, password)
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = home / "config.yaml"
    state_io.write_text(path, yaml.safe_dump({
        "_config_version": 51,
        "terminal": {
            "docker_image": "nousresearch/hermes-sandbox:desktop",
            "modal_image": "ghcr.io/operator/custom:chosen",
            "daytona_image": "nousresearch/hermes-sandbox:desktop",
            "singularity_image": "docker://nousresearch/hermes-sandbox:desktop",
            "cwd": "/chosen/workspace",
        },
        "model": {"default": "chosen-model"},
    }), purpose="config")
    run_migrations(51, {"config_added": [], "env_added": [], "warnings": []}, quiet=True)
    raw = path.read_bytes()
    assert raw.startswith(b"HRMVAULT\x00")
    cfg = yaml.safe_load(state_io.read_text(path, purpose="config"))
    assert cfg["terminal"] == {"modal_image": "ghcr.io/operator/custom:chosen", "cwd": "/chosen/workspace"}
    assert cfg["model"] == {"default": "chosen-model"}
