"""Safety boundary for native updater command tests."""
import pytest


@pytest.fixture(autouse=True)
def isolated_command_checkout(tmp_path, monkeypatch):
    """Even a broken command seam must never target the shared source checkout."""
    from hermes_cli import main
    root = tmp_path / "command-checkout"
    root.mkdir()
    monkeypatch.setattr(main, "PROJECT_ROOT", root)
    monkeypatch.setattr("hermes_cli.update_owning_install.retarget_to_owning_install", lambda root: None)
