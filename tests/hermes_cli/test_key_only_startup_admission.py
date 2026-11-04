"""Public CLI argument refusal precedes every persisted bootstrap side effect.

Run product-entrypoint contracts only on the disposable native CI substrate.
The homes, operator credential and recovery archive here are wholly synthetic.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.platforms('linux', 'macos')


def _tree(root):
    result = {}
    for path in root.rglob('*'):
        key = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode & 0o7777
        result[key] = ('link', mode, os.readlink(path)) if path.is_symlink() else (
            ('file', mode, hashlib.sha256(path.read_bytes()).hexdigest()) if path.is_file()
            else ('directory', mode))
    return result


def _environment(root, home):
    (root / 'user').mkdir(exist_ok=True)
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(root / 'user'),
            'HERMES_HOME': str(home), 'HERMES_RUNTIME_DIR': str(root / 'runtime'),
            'LANG': 'C.UTF-8', 'TZ': 'UTC', 'PYTHONDONTWRITEBYTECODE': '1'}


def _cli(env, args, request=None):
    from hermes_platform.managed_supervision import SupervisedChild
    child = SupervisedChild([sys.executable, '-m', 'hermes_cli.main', *args], cwd=ROOT, env=env,
                            stdin=subprocess.PIPE if request is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = child.process.communicate(request, timeout=90)
        return subprocess.CompletedProcess(args, child.wait(timeout=10), out, err)
    except BaseException:
        child.cancel()
        raise


def _seed(home):
    home.mkdir(parents=True)
    (home / 'config.yaml').write_text('agent: {max_turns: 19}\nupdates: {check: false}\n')
    (home / 'SOUL.md').write_text('synthetic retained operator persona')


def _selection(root, selection):
    home = root / 'state'
    _seed(home)
    selected, flags, suffix = home, [], []
    if selection != 'default':
        selected = home / 'profiles' / 'work'
        _seed(selected)
    if selection == 'active':
        (home / 'active_profile').write_text('work\n')
    if selection == 'profile-home':
        home = selected
    if selection == 'root-alias':
        alias = root / 'home-alias'
        alias.symlink_to(home, target_is_directory=True)
        home = alias
    flags = {'explicit-before': ['--profile=Work'], 'root-alias': ['--profile', 'work']}.get(selection, [])
    suffix = {'explicit-after': ['-p', 'work']}.get(selection, [])
    return home, selected, flags, suffix


def _alias_output(root, selected, _existing):
    alias = root / 'selected-alias'
    alias.symlink_to(selected, target_is_directory=True)
    return alias / 'new.key'


def _link_output(root, _selected, existing, *, hard=False):
    target = root / 'external-new.key'
    if hard:
        target.hardlink_to(existing)
    else:
        target.symlink_to(existing)
    return target


def _foreign_output(root, _selected, _existing):
    from hermes_security import vault
    foreign = root / 'foreign'
    vault.init_vault(foreign, 'synthetic foreign admission credential')
    return foreign / 'new.key'


def _unwritable_output(root, _selected, _existing):
    assert os.geteuid() != 0, 'This permission contract requires the actual unprivileged native user'
    parent = root / 'read-only'
    parent.mkdir(mode=0o500)
    return parent / 'new.key'


_OUTPUTS = {
    'omitted': lambda _root, _selected, _existing: None,
    'inside': lambda _root, selected, _existing: selected / 'vault.key',
    'alias': _alias_output,
    'existing': lambda _root, _selected, existing: existing,
    'symlink': _link_output,
    'hardlink': lambda root, selected, existing: _link_output(root, selected, existing, hard=True),
    'foreign': _foreign_output,
    'unwritable': _unwritable_output,
}


@pytest.mark.parametrize('selection', ['default', 'active', 'explicit-before', 'explicit-after',
                                        'profile-home', 'root-alias'])
@pytest.mark.parametrize('kind', [
    'omitted', 'inside', 'alias', 'existing', 'symlink', 'hardlink', 'foreign',
    pytest.param('unwritable', marks=pytest.mark.skipif(
        not hasattr(os, 'geteuid') or os.geteuid() == 0,
        reason='Unwritable-directory admission requires the actual unprivileged native user')),
])
def test_cold_unsafe_key_only_arguments_leave_entire_selected_world_unchanged(tmp_path, selection, kind):
    from hermes_security import vault
    root = tmp_path / kind
    root.mkdir()
    home, selected, flags, suffix = _selection(root, selection)
    env = _environment(root, home)
    existing = root / 'existing-credential'
    existing.write_bytes(b'synthetic existing credential preserved')
    (root / 'retained-backup.tar').write_bytes(b'synthetic existing archive preserved')
    target = _OUTPUTS[kind](root, selected, existing)
    before = _tree(root)
    args = [*flags, 'secure-vault', 'migrate', '--key-only', '--yes', *suffix]
    if target is not None:
        args += ['--key-out', str(target)]
    try:
        result = _cli(env, args)
        observed = {'selection': selection, 'kind': kind, 'exit': result.returncode,
                    'no_added_changed_removed_files_or_directories': _tree(root) == before}
        assert result.returncode == 2 and observed['no_added_changed_removed_files_or_directories'], observed
    finally:
        vault.clear_vault_cache()


def test_cold_help_is_read_only_and_external_credential_still_unlocks_a_fresh_process(tmp_path):
    home = tmp_path / 'state'
    _seed(home)
    env = _environment(tmp_path, home)
    before = _tree(tmp_path)
    help_result = _cli(env, ['secure-vault', 'migrate', '--key-only', '--help'])
    assert help_result.returncode == 0 and b'--key-out' in help_result.stdout
    assert _tree(tmp_path) == before
    credential = tmp_path / 'operator-selected.key'
    migration = _cli(env, ['secure-vault', 'migrate', '--key-only', '--yes', '--key-out', str(credential)])
    assert migration.returncode == 0
    assert credential.stat().st_mode & 0o777 == 0o600
    assert not credential.read_bytes().startswith(b'HRMVAULT')
    assert (home / 'config.yaml').read_bytes().startswith(b'HRMVAULT')
    unlocked = _cli(env, ['secure-vault', 'unlock', '--private-key', str(credential)])
    managed = _cli(dict(env, HERMES_VAULT_PRIVATE_KEY=str(credential)), ['manage', 'apply'],
                   b'{"op":"config.get","path":["agent","max_turns"]}')
    assert unlocked.returncode == managed.returncode == 0
    assert json.loads(managed.stdout)['result']['value'] == 19
    assert credential.read_bytes().strip() not in migration.stdout + migration.stderr + unlocked.stdout + unlocked.stderr + managed.stdout + managed.stderr
