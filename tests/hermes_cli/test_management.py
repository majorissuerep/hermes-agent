"""Encrypted, typed management contracts; no installed homes or public child processes."""
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from hermes_cli import management
from hermes_cli.management_config import ManagementError


@pytest.fixture
def home(tmp_path, monkeypatch):
    from hermes_security import vault
    from hermes_security.migrate import migrate_home
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text('# keep my comment\nagent:\n  max_turns: 10\ncustom:\n  "dotted.key": [one, two]\n')
    (tmp_path / ".env").write_text("EXISTING_KEY=opaque-original\n")
    migrate_home(tmp_path, "test-password")
    yield tmp_path
    vault.lock_now(tmp_path)


def read(home):
    return management.execute({"op": "config.get", "path": []}, home)


def secret_pipe(value):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, value.encode())
    os.close(write_fd)
    os.set_inheritable(read_fd, True)
    return read_fd


def test_typed_batch_preserves_comments_unrelated_keys_and_ciphertext(home):
    from hermes_security.io import read_text
    before = read(home)
    result = management.execute({"op": "config.batch", "revision": before["revision"], "operations": [
        {"op": "config.set", "path": ["custom", "dotted.key", 1], "value": {"nested": True}},
        {"op": "config.set", "path": ["agent", "max_turns"], "value": 12},
        {"op": "config.unset", "path": ["custom", "dotted.key", 0]},
    ]}, home)
    assert result["effect"] == "next_session"
    raw = read_text(home / "config.yaml", purpose="config")
    assert "# keep my comment" in raw
    assert read(home)["value"] == {"agent": {"max_turns": 12}, "custom": {"dotted.key": [{"nested": True}]}}
    assert b"dotted.key" not in (home / "config.yaml").read_bytes()
    assert "EXISTING_KEY=opaque-original" in read_text(home / ".env", purpose="env")


def test_invalid_batch_and_stale_revision_never_partially_publish(home):
    original = (home / "config.yaml").read_bytes()
    with pytest.raises(ManagementError, match="incompatible type"):
        management.execute({"op": "config.batch", "revision": read(home)["revision"], "operations": [
            {"op": "config.set", "path": ["custom", "first"], "value": "would-be-partial"},
            {"op": "config.set", "path": ["agent", "api_max_retries"], "value": "rejected-sensitive-value"},
        ]}, home)
    assert (home / "config.yaml").read_bytes() == original
    with pytest.raises(ManagementError, match="changed"):
        management.execute({"op": "config.unset", "path": ["agent"], "revision": "stale"}, home)
    assert (home / "config.yaml").read_bytes() == original


def test_parallel_cas_has_one_winner_and_legacy_write_inside_validation_is_not_lost(home, monkeypatch):
    from hermes_cli import config, management_config
    snapshot = read(home)["revision"]
    start = Barrier(2)
    def update(number):
        start.wait()
        try:
            management.execute({"op": "config.set", "path": ["custom", str(number)],
                                "value": number, "revision": snapshot}, home)
            return "committed"
        except ManagementError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, [1, 2]))
    assert sorted(outcomes) == ["committed", "conflict"]
    validate = management_config._validate
    def concurrent_legacy(candidate):
        validate(candidate)
        config.set_config_value("custom.legacy", "true", force=True)
    monkeypatch.setattr(management_config, "_validate", concurrent_legacy)
    with pytest.raises(ManagementError, match="during validation"):
        management.execute({"op": "config.set", "path": ["custom", "managed"], "value": True,
                            "revision": read(home)["revision"]}, home)
    assert read(home)["value"]["custom"]["legacy"] is True
    assert "managed" not in read(home)["value"]["custom"]


def test_redacted_raw_inspection_never_expands_headers_urls_or_nested_secrets(home):
    from hermes_security.io import write_text
    write_text(home / "config.yaml", json.dumps({"providers": {"my.route": {
        "headers": {"X-Opaque": "literal-header-value"}, "api_key": "literal-key",
        "base_url": "https://user:literal-password@example.test/v1?access=literal-query",
        "label": "prefix-${EXISTING_KEY}"}}, "nested": [{"token": "literal-token"}]}), purpose="config")
    rendered = json.dumps(read(home))
    for private in ("literal-header-value", "literal-key", "literal-password", "literal-query", "literal-token", "EXISTING_KEY"):
        assert private not in rendered
    assert "example.test/v1" in rendered
    result = management.execute({"op": "config.get", "path": ["providers", "my.route", "api_key"]}, home)
    assert result["value"] == "[redacted]"


def test_secret_fd_is_closed_without_echo_and_unrelated_secrets_survive(home):
    from hermes_security.io import read_text
    fd = secret_pipe("opaque-new-value")
    result = management.execute({"op": "secret.set", "name": "CUSTOM_API_KEY", "secret_fd": fd}, home)
    with pytest.raises(OSError):
        os.fstat(fd)
    assert "opaque" not in json.dumps(result)
    contents = read_text(home / ".env", purpose="env")
    assert "opaque-new-value" in contents and "opaque-original" in contents
    assert b"opaque" not in (home / ".env").read_bytes()
    assert (home / ".env").stat().st_mode & 0o777 == 0o600
    management.execute({"op": "secret.unset", "name": "CUSTOM_API_KEY"}, home)
    assert "CUSTOM_API_KEY" not in read_text(home / ".env", purpose="env")


def test_public_headers_plugin_keys_and_credential_references_remain_configurable(home):
    request = {"op": "config.set", "path": ["providers", "my.route"], "revision": read(home)["revision"],
               "value": {"headers": {"User-Agent": "Hermes", "Authorization": "Bearer ${CUSTOM_API_KEY}",
                                     "Content-Type": "application/json"}, "key_env": "CUSTOM_API_KEY",
                         "key": "public-plugin-selection", "public_key_path": "/keys/public.pem"}}
    management.execute(request, home)
    from hermes_cli.config import require_readable_config_before_write
    assert require_readable_config_before_write()["providers"]["my.route"] == request["value"]
    before = (home / "config.yaml").read_bytes()
    for value in ("Bearer real-literal-secret", "literal-secret${DUMMY}", "${DUMMY}literal-secret"):
        with pytest.raises(ManagementError, match="store secrets"):
            management.execute({"op": "config.set", "path": ["providers", "my.route", "headers", "Authorization"],
                                "revision": read(home)["revision"], "value": value}, home)
    assert (home / "config.yaml").read_bytes() == before


def test_json_errors_schema_and_locked_symlink_refusals(home, monkeypatch, capsys):
    from hermes_security import vault
    vault.lock_now(home)
    monkeypatch.setattr("hermes_cli.vault_gate._tty_available", lambda: False)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"op":"config.get"}'))
    assert management.main(["apply"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert management.main(["schema"]) == 0
    assert "passphrase.change" in json.loads(capsys.readouterr().out)["result"]["operations"]
    monkeypatch.setattr("sys.stdin", io.StringIO('{"op":"unknown","value":"DO-NOT-ECHO"}'))
    assert management.main(["apply"]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["error"]["code"] == "invalid_operation" and "DO-NOT-ECHO" not in output
    (home / "config.yaml").unlink()
    (home / "config.yaml").symlink_to(home / "elsewhere")
    with pytest.raises(ManagementError, match="symbolic"):
        management.execute({"op": "config.get"}, home)


def test_external_key_slot_and_secure_prompt_use_native_unlock_proof(home, monkeypatch):
    import base64
    from hermes_security import vault
    private, public = vault.generate_keypair()
    vault.add_key_slot(home, public_key_raw=public, unlocked_vault=vault.get_vault(home))
    key_path = home.parent / "synthetic-key-slot"
    key_path.write_text(base64.b64encode(private).decode())
    key_path.chmod(0o600)
    vault.lock_now(home)
    monkeypatch.setenv("HERMES_VAULT_PRIVATE_KEY", str(key_path))
    assert read(home)["value"]["agent"]["max_turns"] == 10
    assert vault.is_unlocked(home)
    vault.lock_now(home)
    monkeypatch.delenv("HERMES_VAULT_PRIVATE_KEY")
    monkeypatch.setattr("hermes_cli.vault_cmd._password_from_env_or_prompt", lambda: "test-password")
    assert read(home)["value"]["agent"]["max_turns"] == 10


def test_admin_pins_refuse_before_any_config_or_secret_publication(home, monkeypatch):
    from hermes_cli import managed_scope, config
    config_before, env_before = (home / "config.yaml").read_bytes(), (home / ".env").read_bytes()
    monkeypatch.setattr(managed_scope, "managed_config_keys", lambda: {"agent.max_turns"})
    with pytest.raises(ManagementError, match="managed"):
        management.execute({"op": "config.set", "path": ["agent"], "value": {"max_turns": 20},
                            "revision": read(home)["revision"]}, home)
    monkeypatch.setattr(config, "env_write_refusal", lambda name, action: "administrator pinned this secret")
    fd = secret_pipe("should-not-publish")
    with pytest.raises(ValueError, match="administrator"):
        management.execute({"op": "secret.set", "name": "CUSTOM_API_KEY", "secret_fd": fd}, home)
    with pytest.raises(OSError):
        os.fstat(fd)
    assert (home / "config.yaml").read_bytes() == config_before
    assert (home / ".env").read_bytes() == env_before


def test_an_explicit_target_cannot_borrow_the_launch_profiles_writers(home):
    with pytest.raises(ManagementError, match="owning home"):
        management.execute({"op": "config.get"}, home / "other-profile")


@pytest.mark.platforms("linux", "macos")
def test_held_open_secret_pipe_times_out_as_json_without_publishing(home, monkeypatch, capsys):
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    os.write(write_fd, b"partial-never-published-secret")
    before = {name: (home / name).read_bytes() for name in (".hermes-vault", ".env", "config.yaml")}
    request = {"op": "secret.set", "name": "CUSTOM_API_KEY", "secret_fd": read_fd}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    try:
        assert management.main(["apply"]) == 2
        response = json.loads(capsys.readouterr().out)
        assert response["error"]["code"] == "secret_timeout"
        assert "partial-never-published-secret" not in json.dumps(response)
        assert {name: (home / name).read_bytes() for name in before} == before
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        os.close(write_fd)


@pytest.mark.parametrize("argv,expected", [
    (["manage", "apply"], ["apply"]),
    (["-p", "work", "manage", "apply"], ["--profile", "work", "apply"]),
    (["--profile", "work", "manage", "apply"], ["--profile", "work", "apply"]),
    (["--profile=work", "manage", "apply"], ["--profile=work", "apply"]),
])
def test_early_management_dispatch_preserves_global_profile_selection(argv, expected, monkeypatch):
    from hermes_cli._startup_fast import try_management
    seen = []
    monkeypatch.setattr(management, "main", lambda args: seen.append(args) or 17)
    with pytest.raises(SystemExit) as stopped:
        try_management(argv)
    assert stopped.value.code == 17 and seen == [expected]
    assert try_management(["chat", "-q", "manage"]) is None
