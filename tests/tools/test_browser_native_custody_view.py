"""The real native client guard preserves the full captured storage-custody interface.

Vault/profile/archive operations are real; RAM backing is the labelled unit seam. No vendor is
launched: the production client_guard surrounds an inert byte producer rather than being mocked.
"""
from pathlib import Path

import pytest

pytest_plugins = ['tests.tools.test_browser_native_private_state']
pytestmark = pytest.mark.platforms('linux', 'macos')


@pytest.mark.parametrize('layout',['own-vault','parent-key-profile'])
def test_real_native_client_guard_keeps_logical_profile_and_original_source(native_state, monkeypatch, layout):
    from agent.session_context import scoped_current_session_id
    from hermes_cli import profiles
    from hermes_security import artifacts,browser_worker_archive as archive,browser_worker_pending as pending,io,vault
    from hermes_security.profile_binding import capture
    from hermes_security.session_authority import start_session_authority
    from tools import browser_native_runtime as runtime,browser_tool_session as session

    parent,_user,ram=native_state
    home=parent
    if layout=='parent-key-profile':
        io.write_text(parent/'config.yaml','model: {provider: litellm, default: fixture}\n',purpose='config')
        io.write_text(parent/'.env','SYNTHETIC_PROFILE_VALUE=unit fixture\n',purpose='env')
        home=profiles.create_profile('native-child',no_alias=True,no_skills=True)
        monkeypatch.setenv('HERMES_HOME',str(home))
    first=start_session_authority(home,'native-client-view-A')
    sibling=start_session_authority(home,'native-client-view-B')
    payload=b'complete original native client progress'
    artifact=home/'cache/spillover/native-client.txt'
    try:
        with scoped_current_session_id(first.session_id):
            socket=session._prepare_session_socket_dir('native-client-view')
            original=session._agent_browser_command_env(socket)
            selected=original.lease.resource
            if layout=='parent-key-profile':
                # Exercise reopening an existing metadata envelope through the native view too.
                with selected.operation(operation=original.operation):
                    pass
            target=Path(original['HOME'])/'private-progress.tmp'
            with runtime.client_guard(original,['declared-inert-native-vendor']):
                target.write_bytes(payload)
                artifacts.write_bytes(artifact,payload,home=home,custody=original)
            assert original.home==home and original.vault.home==parent
            document=pending.read(home,pending.relative(selected.digest),custody=capture(home))
            assert document['home']==str(home)
            cipher=(home/selected.relative).read_bytes()
            artifact_cipher=artifact.read_bytes()
            assert payload not in cipher and payload not in artifact_cipher
            if layout=='parent-key-profile':
                raw=(home/pending.relative(selected.digest)).read_bytes()
                with pytest.raises(vault.VaultError):
                    # The physical machine key with an unscoped child-relative AAD is insufficient.
                    vault.get_vault(parent).decrypt(raw,purpose='state',relpath=pending.relative(selected.digest).as_posix())
        first.close()
        with scoped_current_session_id(sibling.session_id):
            with pytest.raises(vault.VaultError):
                with runtime.client_guard(original,['declared-inert-native-vendor']):
                    pytest.fail('A live sibling revived the original native operation')
            with pytest.raises(vault.VaultError):
                artifacts.write_bytes(artifact,b'refused old callback',home=home,custody=original)
            assert (home/selected.relative).read_bytes()==cipher and artifact.read_bytes()==artifact_cipher
            fresh=session._agent_browser_command_env(socket)
            with runtime.client_guard(fresh,['declared-inert-native-vendor']):
                assert target.read_bytes()==payload
                target.write_bytes(payload+b' saved by B')
            readback=ram/'native-client-full-readback';readback.mkdir(mode=0o700)
            archive.replay_archive(home,selected.relative,custody=fresh,target=readback)
            assert (readback/'home/private-progress.tmp').read_bytes()==payload+b' saved by B'
            assert sibling.is_live() and vault.is_unlocked(parent)
    finally:
        first.close();sibling.close()
