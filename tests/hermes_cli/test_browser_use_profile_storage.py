"""Named profiles share a physical key without sharing Browser Use state or import authority.

The RAM and vendor-idle facts are declared unit seams; archive and profile authentication are real.
"""
from types import SimpleNamespace

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


@pytest.fixture
def named(native_state, monkeypatch):
    from hermes_cli import profiles
    from hermes_security import io

    home, _user, _ram = native_state
    monkeypatch.setattr(profiles, '_get_default_hermes_home', lambda: home)
    monkeypatch.setattr(profiles, '_get_profiles_root', lambda: home/'profiles')
    io.write_text(home/'config.yaml', 'model: {provider: litellm, default: fixture}\n', purpose='config')
    io.write_text(home/'.env', 'SYNTHETIC_PROFILE_VALUE=unit fixture\n', purpose='env')
    return tuple(profiles.create_profile(name, no_alias=True, no_skills=True) for name in ('first', 'second'))


def test_selected_parent_key_profile_import_does_only_its_requested_conversion(native_state, named, monkeypatch):
    from hermes_cli import browser_use_import as imports
    from hermes_cli.vault_repair import run
    from hermes_security import browser_worker_archive as archive, vault

    parent, user, ram = native_state
    first, second = named
    source = user/'explicit-selected-source'; source.mkdir(mode=0o700)
    payload = b'complete named profile source without executing its helper'
    (source/'agent_helpers.py').write_bytes(payload)
    monkeypatch.setattr(imports, 'require_harness_source_idle', lambda *_a, **_kw: None)
    args = SimpleNamespace(browser_use_workspace=str(source), browser_use_config=None, browser_use_task=None)
    assert run(args, first) == 0
    selected = imports.mapped_resource(first, source, 'workspace')
    assert selected.custody.home == first and selected.custody.vault.home == parent
    assert not source.exists() and not (first/'.hermes-vault').exists()
    with selected.operation():
        assert (selected.tree/'agent_helpers.py').read_bytes() == payload
    cipher = (first/selected.relative).read_bytes()
    assert payload not in cipher and not (second/selected.relative).exists()
    (second/selected.relative).parent.mkdir(mode=0o700)
    (second/selected.relative).write_bytes(cipher)
    (second/selected.relative).chmod(0o600)
    with pytest.raises(vault.VaultError):
        archive.replay_archive(second, selected.relative, custody=archive.capture_custody(second))
    assert (second/selected.relative).read_bytes() == cipher
    before = (first/selected.relative).read_bytes()
    damaged = first/'.env'; damaged.write_bytes(b'damaged original preferred identity')
    assert run(args, first) == 1
    assert (first/selected.relative).read_bytes() == before
    assert selected.tree.is_relative_to(ram) and vault.is_unlocked(parent)
    archive.capture_custody(second).check()


def test_shared_key_does_not_authorize_foreign_import_or_same_ram_workspace(native_state, named):
    from hermes_cli import browser_use_import as imports, browser_worker_store as store
    from hermes_security import vault

    parent, _user, ram = native_state
    first, second = named
    old = second/'cache/browser-use/workspace/default'
    old.mkdir(mode=0o700, parents=True)
    (old/'only-copy').write_bytes(b'second profile only')
    before = (old/'only-copy').stat()
    with pytest.raises(vault.VaultError):
        imports.import_source(first, old, kind='workspace', task='default')
    assert (old/'only-copy').read_bytes() == b'second profile only'
    assert (old/'only-copy').stat().st_ino == before.st_ino
    shared = ram/'explicit-shared-path'; shared.mkdir(mode=0o700)
    selected = store.resource(first, kind='browser-use-explicit-tree', external_tree=shared)
    with selected.operation():
        (shared/'state').write_bytes(b'first profile private workspace')
    cipher = (first/selected.relative).read_bytes()
    other = store.resource(second, kind='browser-use-explicit-tree', external_tree=shared)
    with pytest.raises(store.WorkerStateError):
        with other.operation():
            pytest.fail('Shared physical key authorized a foreign logical workspace')
    assert (shared/'state').read_bytes() == b'first profile private workspace'
    assert (first/selected.relative).read_bytes() == cipher and vault.is_unlocked(parent)
