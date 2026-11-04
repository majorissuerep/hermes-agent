"""Tests for CLI browser CDP auto-launch helpers."""

from unittest.mock import Mock, patch

import pytest

from hermes_cli.browser_connect import (
    ChromeDebugLaunch,
    _wait_for_browser_debug_ready_or_exit,
    get_chrome_debug_candidates,
    is_browser_debug_ready,
    launch_chrome_debug,
    manual_chrome_debug_command,
)
from tests.hermes_cli.browser_profile_env import chromium_tree, owned_profile_env, reset_owned_profiles

PREPARED = "/run/user/1000/hermes-browser-profile-0123456789abcdef"


@pytest.fixture
def owned(tmp_path, monkeypatch):
    yield owned_profile_env(tmp_path, monkeypatch)
    reset_owned_profiles()


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestChromeDebugLaunch:
    def test_browser_debug_ready_requires_http_cdp_endpoint(self):
        requested = []

        def fake_urlopen(url, timeout):
            requested.append(url)
            if url.endswith("/json/version"):
                return _FakeResponse()
            raise OSError("unexpected probe")

        with patch("urllib.request.OpenerDirector.open", side_effect=fake_urlopen):
            assert is_browser_debug_ready("http://127.0.0.1:9222", timeout=0.1) is True

        assert requested == ["http://127.0.0.1:9222/json/version"]

    def test_browser_debug_ready_rejects_non_cdp_listener(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=OSError("not cdp")):
            assert is_browser_debug_ready("http://127.0.0.1:9222", timeout=0.1) is False



    def test_manual_command_uses_detected_linux_browser(self):
        with patch("hermes_cli.browser_connect.shutil.which", side_effect=lambda name: "/usr/bin/chromium" if name == "chromium" else None), \
             patch("hermes_cli.browser_connect.os.path.isfile", side_effect=lambda path: path == "/usr/bin/chromium"):
            command = manual_chrome_debug_command(9222, "Linux", PREPARED)

        assert command is not None
        assert command.startswith("/usr/bin/chromium --remote-debugging-port=9222")
        assert f"--user-data-dir={PREPARED}" in command

    def test_manual_command_requires_a_prepared_profile(self):
        with patch("hermes_cli.browser_connect.shutil.which", side_effect=lambda name: "/usr/bin/chromium" if name == "chromium" else None), \
             patch("hermes_cli.browser_connect.os.path.isfile", side_effect=lambda path: path == "/usr/bin/chromium"):
            assert manual_chrome_debug_command(9222, "Linux", None) is None










    def test_linux_candidates_include_official_brave_and_edge_stable_paths(self):
        brave = "/usr/bin/brave-browser-stable"
        edge = "/usr/bin/microsoft-edge-stable"

        with patch("hermes_cli.browser_connect.shutil.which", return_value=None), \
             patch("hermes_cli.browser_connect.os.path.isfile", side_effect=lambda path: path in {brave, edge}):
            candidates = get_chrome_debug_candidates("Linux")

        assert candidates == [brave, edge]


    def test_wsl_install_candidates_keep_posix_separators_on_nt_host(self):
        expected = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"

        with patch("hermes_cli.browser_connect.shutil.which", return_value=None), \
             patch("hermes_cli.browser_connect.os.path.isfile", side_effect=lambda path: path == expected):
            candidates = get_chrome_debug_candidates("Linux")

        assert candidates == [expected]
        assert "\\" not in candidates[0]


    def test_wait_for_browser_debug_ready_or_exit_detects_early_exit(self, monkeypatch):
        class _Proc:
            def __init__(self):
                self.calls = 0

            def poll(self):
                self.calls += 1
                return 1 if self.calls >= 2 else None

        monkeypatch.setattr("hermes_cli.browser_connect.time.sleep", lambda _seconds: None)
        with patch("hermes_cli.browser_connect.is_browser_debug_ready", return_value=False):
            state = _wait_for_browser_debug_ready_or_exit(_Proc(), 9222, timeout=0.3, interval=0.01)

        assert state == "exited"



    def test_launch_result_surfaces_stderr_tail_on_crash(self, owned):
        _home, ram = owned
        chrome = "/usr/bin/google-chrome"
        launched = {}

        class _Proc:
            pid = 4321
            returncode = 127

            def __init__(self, argv, stderr):
                launched["argv"] = argv
                # Simulate the browser writing to the redirected stderr file.
                stderr.write(b"error while loading shared libraries: libnspr4.so\n")
                stderr.flush()

            def poll(self):
                return 127

        with patch("hermes_cli.browser_connect.get_chrome_debug_candidates", return_value=[chrome]), \
             patch("hermes_cli.browser_connect.is_browser_debug_ready", return_value=False), \
             patch("subprocess.Popen", side_effect=lambda argv, **k: _Proc(argv, k["stderr"])):
            result = launch_chrome_debug(9222, "Linux")

        assert result.launched is False
        assert result.attempts[0].returncode == 127
        assert "libnspr4.so" in result.attempts[0].stderr_tail
        assert result.hint is not None
        assert "libnspr4.so" in result.hint
        # The browser ran on the prepared protected-RAM copy, never on a home path.
        assert result.profile_dir and result.profile_dir.startswith(str(ram.resolve()))
        assert f"--user-data-dir={result.profile_dir}" in launched["argv"]

    def test_unpreparable_debug_profile_launches_nothing(self, owned):
        home, _ram = owned
        chromium_tree(home / "chrome-debug")  # an older build's plaintext profile awaiting repair
        popen = Mock()
        with patch("hermes_cli.browser_connect.get_chrome_debug_candidates", return_value=["/usr/bin/chromium"]), \
             patch("subprocess.Popen", popen):
            result = launch_chrome_debug(9222, "Linux")

        popen.assert_not_called()
        assert result.launched is False and result.profile_dir is None
        assert "hermes secure-vault repair" in result.hint

    def test_slash_connect_offers_no_manual_command_for_unpreparable_profile(self, capsys):
        from hermes_cli import cli_browser_launch as mixin

        refusal = ChromeDebugLaunch(profile_error="Hermes keeps active browser profiles only in proven RAM.")
        manual = Mock(return_value="/usr/bin/chromium --user-data-dir=/unprotected")
        with patch.object(mixin, "local_port_in_use", return_value=False), \
             patch.object(mixin, "launch_chrome_debug", return_value=refusal), \
             patch.object(mixin, "manual_chrome_debug_command", manual):
            assert mixin.launch_default_cdp_browser(9222) is None

        manual.assert_not_called()
        printed = capsys.readouterr().out
        assert refusal.profile_error in printed and "--user-data-dir" not in printed

    def test_launch_result_no_hint_when_no_candidates(self):
        with patch("hermes_cli.browser_connect.get_chrome_debug_candidates", return_value=[]):
            result = launch_chrome_debug(9222, "Linux")

        assert result.launched is False
        assert result.attempts == []
        assert result.hint is None

    def test_manual_command_uses_wsl_windows_chrome_when_available(self):
        chrome = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"

        with patch("hermes_cli.browser_connect.shutil.which", return_value=None), \
             patch("hermes_cli.browser_connect.os.path.isfile", side_effect=lambda path: path == chrome):
            command = manual_chrome_debug_command(9222, "Linux", PREPARED)

        assert command is not None
        # Linux/WSL uses POSIX shell quoting (single quotes around paths with spaces).
        assert command.startswith(f"'{chrome}' --remote-debugging-port=9222")

    def test_manual_command_uses_windows_quoting_on_windows(self):
        chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

        with patch("hermes_cli.browser_connect.shutil.which", side_effect=lambda name: chrome if name == "chrome.exe" else None), \
             patch("hermes_cli.browser_connect.os.path.isfile", side_effect=lambda path: path == chrome):
            command = manual_chrome_debug_command(9222, "Windows", PREPARED)

        assert command is not None
        # Windows uses cmd.exe-compatible quoting via subprocess.list2cmdline.
        assert command.startswith(f'"{chrome}" --remote-debugging-port=9222')
        assert "'" not in command


