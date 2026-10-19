"""Public profile creation must publish usable, path-bound encrypted islands."""

import os
from pathlib import Path
import subprocess

import pytest
import hermes_cli

REPO = Path(hermes_cli.__file__).resolve().parents[1]


@pytest.fixture
def encrypted_profile_install(tmp_path):
    from tests.e2e.vault_home import prepare_fixture_runtime

    user = tmp_path / "public-profile-owner"
    user.mkdir()
    home = user / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model:\n  provider: litellm\n  default: deepseek/deepseek-v4-flash-0731\n"
        "agent:\n  max_turns: 31\ncli:\n  expose_on_path: false\nupdates:\n  check: false\n"
    )
    (home / ".env").write_text("ACME_TOKEN=profile-owner-private-fixture\n")
    prepare_fixture_runtime(home, REPO)
    return user, home


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("kind", ["fresh", "clone", "clone-all"])
def test_public_profile_create_publishes_authenticated_independent_state(encrypted_profile_install, kind):
    from hermes_cli._launchers import runtime_command
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_security.io import read_text, write_text
    from tests.e2e.vault_home import run_unlocked

    user, home = encrypted_profile_install
    name = "sealed-" + kind
    args = ["profile", "create", name, "--no-alias"]
    args += ["--no-skills"] if kind == "fresh" else ["--" + kind]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("HERMES_") and not key.endswith(("_API_KEY", "_TOKEN"))}
    env.update(HOME=str(user), HERMES_HOME=str(home), HERMES_STATE_DB_GUARD_BYPASS="1")
    token = set_hermes_home_override(home)
    try:
        argv = runtime_command(REPO, args, home=home)
    finally:
        reset_hermes_home_override(token)
    before = (home / ".env").read_bytes()
    result = run_unlocked(argv, env=env, cwd=user, text=True, capture_output=True,
                          stdin=subprocess.DEVNULL, timeout=180)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Unlock master password:" not in result.stdout + result.stderr
    destination = home / "profiles" / name
    assert destination.is_dir()
    assert not list(destination.parent.glob(f".{name}.staging-*"))
    for filename, purpose in (("config.yaml", "config"), (".env", "env"), ("SOUL.md", "state")):
        text = read_text(destination / filename, purpose=purpose)
        assert text is not None
        assert (destination / filename).read_bytes().startswith(b"HRMVAULT\x00")
    assert not (destination / ".hermes-vault").exists(), "A clone must not copy root vault authority"
    if kind != "fresh":
        assert "profile-owner-private-fixture" in read_text(destination / ".env", purpose="env")
    write_text(destination / ".env", "ACME_TOKEN=independent-profile-fixture\n", purpose="env")
    assert read_text(destination / ".env", purpose="env") == "ACME_TOKEN=independent-profile-fixture\n"
    assert (home / ".env").read_bytes() == before
