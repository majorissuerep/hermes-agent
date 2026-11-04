"""Recording cleanup preserves other leases and obeys the original custody.

Real encryption; existing labelled RAM and outgoing transport unit stand-ins.
"""
import json
import secrets

import pytest

from hermes_security import artifacts, protected_workspace, vault
from tools import browser_recording as recording
from tools import browser_recording_pending as pending
from tools.browser_terminal import BrowserRuntime
from hermes_platform.remote_identity import RuntimeIdentity
from tests.tools.test_browser_recording_custody import (
    PASSWORD, FakeBrowser, FakeOutgoing, _bind_browser,
)

pytest_plugins = ["tests.tools.test_browser_recording_custody"]


def _completed(home, custody, name, payload):
    lease = pending.allocate(home, custody, name)
    pending.store_capture(lease, custody, payload)
    pending.completed(lease, custody)
    return lease


def test_cleanup_must_not_remove_a_replaced_authenticated_pending_locator(owner, tmp_path, monkeypatch):
    home, _ram = owner
    custody = artifacts._bound_custody(home)
    name = 'browser_recording_20261104T120000Z_' + secrets.token_hex(16) + '.webm'
    first, second = b'first-video|' + secrets.token_bytes(40), b'second-video|' + secrets.token_bytes(40)
    lease = _completed(home, custody, name, first)
    unlink = pending._unlink_capture
    replacement = []

    def swap_locator(fd, current, recorded):
        # An independently allocated generation can reuse this valid name on a different
        # protected base after its earlier encrypted locator was moved aside. No key forgery.
        current.locator.rename(current.locator.with_suffix('.previous'))
        newer_ram = tmp_path / 'new-ram-base'
        newer_ram.mkdir(mode=0o700)
        monkeypatch.setattr(protected_workspace, 'protected_workspace_base', lambda: newer_ram)
        newer = _completed(home, custody, name, second)
        assert json.loads(artifacts.read_bytes(newer.locator, home=home))['workspace'] == str(newer.workspace)
        replacement.append(newer)
        unlink(fd, current, recorded)

    monkeypatch.setattr(pending, '_unlink_capture', swap_locator)
    destination, problem = pending.publish_and_retire(lease, custody)
    [newer] = replacement

    assert problem is not None
    assert artifacts.read_bytes(destination, home=home) == first
    assert newer.stage.read_bytes() == second
    assert newer.locator.name in pending.locator_names(home)
    assert newer.locator.exists(), 'Cleanup removed a newer authenticated locator; its different RAM-only capture is undiscoverable'


def test_revocation_after_verified_publication_effects_are_distinct_from_data_loss(owner, monkeypatch):
    home, _ram = owner
    custody = artifacts._bound_custody(home)
    name = 'browser_recording_20261104T120000Z_' + secrets.token_hex(16) + '.webm'
    payload = b'verified-video|' + secrets.token_bytes(40)
    lease = _completed(home, custody, name, payload)
    verify = pending._verify_destination

    def revoke_after_verification(*args, **kwargs):
        verify(*args, **kwargs)
        vault.lock_now(home)

    monkeypatch.setattr(pending, '_verify_destination', revoke_after_verification)
    destination, problem = pending.publish_and_retire(lease, custody)
    from hermes_security.errors import VaultError

    with pytest.raises(VaultError):
        custody.check()
    assert problem is not None
    assert lease.stage.read_bytes() == payload
    assert lease.locator.exists()
    vault.unlock(home, PASSWORD)  # independent human reopen, never given to the old custody
    recovered = artifacts.read_bytes(destination, home=home) == payload

    assert recovered


def test_outgoing_fetch_failure_does_not_claim_an_unconfirmed_local_copy(owner, monkeypatch, caplog):
    _home, _ram = owner
    env = FakeOutgoing()
    runtime = BrowserRuntime('/run/owned', '/run/owned/profile', '/run/owned/captures',
                             '/run/owned/sockets', '/run/owned/tmp', generation=RuntimeIdentity("unit-generation", (("/unit-declared-path", "1000:700:1:1"),)))
    monkeypatch.setattr('tools.terminal_tool._get_env_config', lambda: {'env_type': 'ssh'})
    monkeypatch.setattr('tools.browser_terminal.terminal_environment', lambda create=True: env)
    monkeypatch.setattr('tools.browser_terminal.browser_runtime', lambda _env: runtime)
    monkeypatch.setattr('hermes_platform.remote_workspace.validate', lambda *args, **kwargs: None)
    browser = FakeBrowser(writes_local=False)
    _bind_browser(monkeypatch, browser)

    def fail(_rec):
        raise recording.RecordingError('synthetic original backend retrieval failure')

    monkeypatch.setattr(recording, '_fetch_capture_bytes', fail)
    recording.start('outgoing-heldout')
    rec = recording._RECORDINGS['outgoing-heldout']
    env.files[rec.capture] = b'original-backend-only-video'
    assert recording.stop('outgoing-heldout') is None
    wrong_claim = 'Its only copy stays in protected RAM at ' + str(rec.stage)

    assert not rec.stage.exists() and rec.capture in env.files
    assert "original selected outgoing backend" in caplog.text
    assert rec.capture in caplog.text
    assert len(browser.stops()) == 1
    assert rec.lease.locator.exists()
    assert wrong_claim not in caplog.text, 'Retrieval failure claims a local copy exists, but only the selected backend holds it'
