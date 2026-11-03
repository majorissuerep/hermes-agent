"""Root-run local agents retain sensitive-write approval checks."""
import pytest


@pytest.mark.platforms("posix")
def test_single_component_user_home_requires_sensitive_write_approval(monkeypatch):
    from tools.approval import detect_dangerous_command
    from tools.approval_detection import _rewrite_resolved_user_home

    monkeypatch.setenv("HOME", "/root")
    for target in (".ssh/authorized_keys", ".bashrc"):
        assert detect_dangerous_command(f"echo changed >> /root/{target}")[0]
    assert not detect_dangerous_command("echo note >> /root/notes.txt")[0]
    monkeypatch.setenv("HOME", "/")
    assert _rewrite_resolved_user_home("cat /etc/hosts") == "cat /etc/hosts"
