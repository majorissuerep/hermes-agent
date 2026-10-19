"""Every fresh configuration writer produces terminal-only, editable settings."""
import os
import shutil

import pytest



def _config_edit(tmp_path, monkeypatch, cfg, *, template):
    """`hermes config edit` on a home with no config.yaml (seeds via seed_config_file, like `doctor --fix`)."""
    monkeypatch.setenv("EDITOR", "true")
    monkeypatch.setattr(cfg.subprocess, "run", lambda *a, **k: None)
    if not template:
        monkeypatch.setattr(cfg, "get_project_root", lambda: tmp_path / "no-checkout")
    cfg.edit_config()


def _setup_agent_enter(tmp_path, monkeypatch, cfg):
    """`hermes setup agent` on a fresh home, pressing Enter (the offered default) on every prompt."""
    from hermes_cli import setup

    monkeypatch.setattr(setup, "prompt", lambda question, default=None, *a, **k: default or "")
    monkeypatch.setattr(setup, "prompt_yes_no", lambda *a, **k: False)
    setup.setup_agent_settings(cfg.load_config())


def _apply_default_agent_settings(tmp_path, monkeypatch, cfg):
    """Quick and full first-time setup."""
    from hermes_cli.setup import _apply_default_agent_settings

    _apply_default_agent_settings(cfg.load_config())


def _blank_slate(tmp_path, monkeypatch, cfg):
    from hermes_cli.setup_quick import _blank_slate_minimize_config

    config = cfg.load_config()
    _blank_slate_minimize_config(config)
    cfg.save_config(config)


def _doctor_fix(tmp_path, monkeypatch, cfg):
    """`hermes doctor --fix` on a home with no config.yaml: the real config-file check, fix enabled."""
    from hermes_cli import doctor
    from hermes_cli.doctor_config import _check_config_file

    root = tmp_path / "checkout"  # only the template, so a stray cli-config.yaml cannot short-circuit the seed
    (root / "hermes_cli" / "data").mkdir(parents=True)
    shutil.copy2(cfg.get_project_root() / "hermes_cli/data/config.yaml.example", root / "hermes_cli/data/config.yaml.example")
    home = cfg.get_hermes_home()
    monkeypatch.setattr(doctor, "HERMES_HOME", home)
    monkeypatch.setattr(doctor, "PROJECT_ROOT", root)
    assert _check_config_file(True).fixed == 1
    if os.name == "posix" and not cfg._container_or_chmod_skipped():
        assert (home / "config.yaml").stat().st_mode & 0o777 == 0o600


SEEDERS = {
    "config-edit-template": lambda *a: _config_edit(*a, template=True),
    "config-edit-no-template": lambda *a: _config_edit(*a, template=False),
    "setup-agent-enter": _setup_agent_enter,
    "apply-default-agent-settings": _apply_default_agent_settings,
    "blank-slate": _blank_slate,
    "doctor-fix": _doctor_fix,
}


@pytest.mark.parametrize("seeder", list(SEEDERS))
def test_every_seeder_produces_terminal_configuration(tmp_path, monkeypatch, seeder):
    import hermes_cli.config as cfg

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    SEEDERS[seeder](tmp_path, monkeypatch, cfg)

    config_path = home / "config.yaml"
    assert config_path.exists()  # the resolution checks below would pass on a missing file
    raw = cfg.read_user_config_raw(config_path)
    assert isinstance(raw, dict)
    assert not ({"gateway", "platforms", "desktop", "dashboard"} & raw.keys())


def test_readonly_packaged_template_does_not_make_new_managed_config_readonly(tmp_path, monkeypatch):
    from hermes_cli import config

    home = tmp_path / "managed-home"
    home.mkdir()
    (home / ".managed").write_text("nixos")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_MANAGED", raising=False)
    template = tmp_path / "packaged-template.yaml"
    template.write_text("model:\n  default: safe-model\n")
    template.chmod(0o444)
    target = home / "config.yaml"
    assert config.seed_config_file(target, template)
    assert target.read_text() == template.read_text()
    assert os.access(target, os.W_OK)
    with target.open("a") as handle:
        handle.write("# user edit\n")
    assert target.read_text().endswith("# user edit\n")
