"""Generated file scripts retain their dialect on native non-Bash terminals."""

import shutil

import pytest

from tools.environments.local import LocalEnvironment
from tools.file_operations import ShellFileOperations


pytestmark = pytest.mark.platforms("linux", "macos")


def test_generated_file_search_uses_bash_without_changing_user_shell(tmp_path, monkeypatch):
    environment = LocalEnvironment(cwd=str(tmp_path))
    operations = ShellFileOperations(environment)
    target = tmp_path / "vault"
    target.mkdir()
    (tmp_path / "vault.txt").write_text("payload")
    # Exercise the portable generated find pipeline, not the local native rg path.
    monkeypatch.setattr(operations, "_has_command", lambda command: command == "find")
    try:
        result = operations.search("vault*", path=str(tmp_path), target="files")
        assert result.error is None, result
        assert set(result.files) == {str(target), str(tmp_path / "vault.txt")}
        missing = operations.delete_file(str(target))
        assert missing.error is not None
        assert target.is_dir()
    finally:
        environment.cleanup()


def test_generated_heredoc_write_keeps_exact_bytes(tmp_path, monkeypatch):
    assert shutil.which("bash"), "file scripts require Bash on the selected backend"
    environment = LocalEnvironment(cwd=str(tmp_path))
    monkeypatch.setattr(environment, "_stdin_mode", "heredoc")
    operations = ShellFileOperations(environment)
    path = tmp_path / "quoted ' content.txt"
    content = "literal ' \" $(not_a_command) `neither` \\ no final newline"
    try:
        result = operations.write_file(str(path), content)
        assert result.error is None, result
        assert path.read_bytes() == content.encode()
    finally:
        environment.cleanup()
