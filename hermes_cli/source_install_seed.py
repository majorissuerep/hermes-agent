"""Seed only absent installer defaults, using the encrypted owner's writers."""
from __future__ import annotations

from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def seed_encrypted_install_state(home: Path) -> None:
    from pm.state_io import prepare_owner

    prepare_owner()
    from hermes_cli.config import _write_env_lines, seed_config_file

    if not (home / "config.yaml").exists():
        seed_config_file(home / "config.yaml")
    if not (home / ".env").exists():
        template = Path(__file__).with_name("data") / "env.example"
        _write_env_lines(home / ".env", template.read_text(encoding="utf-8").splitlines(keepends=True),
                         preserve_mode=False)


if __name__ == "__main__":
    seed_encrypted_install_state(Path(sys.argv[1]))
