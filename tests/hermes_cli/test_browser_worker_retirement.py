"""Interrupted cleanup preserves the full verified worker snapshot and original source."""
from pathlib import Path
import pytest
pytest_plugins=['tests.tools.test_browser_native_private_state']
pytestmark=pytest.mark.platforms('linux','macos')

@pytest.mark.parametrize('interruption',['binding','payload'])
def test_interrupted_retirement_remains_repairable_without_overwriting_complete_checkpoint(native_state,monkeypatch,interruption):
    from hermes_cli import browser_worker_store as store
    from hermes_cli import browser_worker_retirement as retirement
    from hermes_security import browser_worker_archive as archive
    from hermes_security import browser_worker_pending as pending
    home,_user,ram=native_state
    selected=store.resource(home,kind='worker-interrupted-retirement')
    with selected.operation():
        (selected.tree/'sole-copy').write_bytes(b'complete original private worker progress')
    selected.release()
    remove=retirement._remove_target
    def interrupted(target_record,custody):
        path=Path(target_record['path'])
        if path==selected.path:
            target=path/'.worker-binding.json' if interruption=='binding' else path/'tree'/'sole-copy'
            target.unlink()
            raise OSError('declared interrupted RAM retirement')
        return remove(target_record,custody)
    monkeypatch.setattr(retirement,'_remove_target',interrupted)
    first=store.retire_quiescent(home)
    assert first['retained'] and not first['archived']
    original=(home/selected.relative).read_bytes()
    monkeypatch.setattr(retirement,'_remove_target',remove)
    second=store.retire_quiescent(home)
    assert second['archived']==[selected.relative.as_posix()] and not second['retained']
    readback=ram/'verified-whole-readback';readback.mkdir(mode=0o700)
    archive.replay_archive(home,selected.relative,custody=archive.capture_custody(home),target=readback)
    assert (readback/'sole-copy').is_file(), 'Complete verified checkpoint lost a file after interrupted cleanup'
    assert (readback/'sole-copy').read_bytes()==b'complete original private worker progress'
    assert (home/selected.relative).read_bytes()==original
    histories=pending.paths(home,kind='recovery')
    assert histories
    for history in histories:
        with history.open('rb') as handle:
            # The same opened-FD authentication consumer is used by opaque backup/import.
            record=archive.authenticate_ciphertext(handle,history.relative_to(home),custody=archive.capture_custody(home))
        assert 'original checkpoint unchanged' in record['outcome']


@pytest.mark.parametrize('mutation',['directory','cipher-before-resume','cipher-after-auth','source-close'])
def test_retirement_refuses_replaced_targets_changed_cipher_or_revoked_source(native_state,monkeypatch,mutation):
    from agent.session_context import scoped_current_session_id
    from hermes_cli import browser_worker_retirement as retirement,browser_worker_store as store
    from hermes_security import vault
    from hermes_security.session_authority import start_session_authority
    from hermes_security import browser_worker_pending as pending

    home,_user,ram=native_state
    first=start_session_authority(home,'retirement-source-A')
    sibling=start_session_authority(home,'retirement-source-B')
    try:
        with scoped_current_session_id('retirement-source-A'):
            selected=store.resource(home,kind='changed-retirement-state')
            with selected.operation():
                (selected.tree/'sole-copy').write_bytes(b'complete retained private progress')
            selected.release()
            remove=retirement._remove_target
            def interrupted(*_a):
                raise OSError('declared interruption after verified intent')
            monkeypatch.setattr(retirement,'_remove_target',interrupted)
            assert store.retire_quiescent(home)['retained']
            monkeypatch.setattr(retirement,'_remove_target',remove)
            cipher=home/selected.relative
            original=cipher.read_bytes()
            retained=selected.path
            def replace_cipher():
                data=bytearray(cipher.read_bytes());data[-1]^=1
                replacement=cipher.parent/'.test-replacement'
                replacement.write_bytes(data);replacement.chmod(0o600);replacement.replace(cipher)
            if mutation=='directory':
                retained=ram/'original-retained-root'
                selected.path.rename(retained)
                selected.path.mkdir(mode=0o700)
                (selected.path/'replacement').write_bytes(b'unrelated replacement must remain')
            elif mutation=='cipher-before-resume':
                replace_cipher()
            elif mutation in {'cipher-after-auth','source-close'}:
                def changed_after_auth(target,custody):
                    if mutation=='source-close':
                        first.close()
                    else:
                        replace_cipher()
                    return remove(target,custody)
                monkeypatch.setattr(retirement,'_remove_target',changed_after_auth)
            refused=store.retire_quiescent(home)
            assert refused['retained'] and not refused['archived'] and pending.paths(home)
            assert (retained/'tree'/'sole-copy').read_bytes()==b'complete retained private progress'
            if mutation=='directory':
                assert (selected.path/'replacement').read_bytes()==b'unrelated replacement must remain'
        assert sibling.is_live() and vault.is_unlocked(home)
        if mutation=='source-close':
            monkeypatch.setattr(retirement,'_remove_target',remove)
            with scoped_current_session_id('retirement-source-B'):
                assert store.retire_quiescent(home)['archived']==[selected.relative.as_posix()]
            assert cipher.read_bytes()==original and not selected.path.exists()
    finally:
        first.close();sibling.close()
