"""Local profile cloning preserves provider state and strips historical channel identity."""
from pathlib import Path
import hermes_yaml as yaml


def test_terminal_clone_drops_retired_channel_identity_only(tmp_path, monkeypatch):
    from hermes_cli.profile_channels import strip_channel_settings
    source = tmp_path / 'source'
    clone = tmp_path / 'clone'
    source.mkdir()
    clone.mkdir()
    text = ('OPENAI_API_KEY=provider-test\nTELEGRAM_BOT_TOKEN=old-bot\n'
            'FIRECRAWL_API_KEY=tool-test\nTWILIO_AUTH_TOKEN=tool-phone\n')
    (source / '.env').write_text(text)
    (clone / '.env').write_text(text)
    (clone / 'config.yaml').write_text(yaml.safe_dump({
        'model': {'default': 'provider/model'}, 'platforms': {'telegram': {'enabled': True}},
        'gateway': {'multiplex_profiles': True}, 'memory': {'provider': 'builtin'}}))
    stripped = strip_channel_settings(clone, include_state=False, source_dir=source)
    result = (clone / '.env').read_text()
    assert 'TELEGRAM_BOT_TOKEN' not in result
    assert 'OPENAI_API_KEY=provider-test' in result
    assert 'FIRECRAWL_API_KEY=tool-test' in result
    assert 'TWILIO_AUTH_TOKEN=tool-phone' in result
    assert (source / '.env').read_text() == text
    config = yaml.safe_load((clone / 'config.yaml').read_text())
    assert config['model']['default'] == 'provider/model'
    assert config['memory'] == {'provider': 'builtin'}
    assert 'platforms' not in config
    assert 'telegram' in stripped


def test_local_profile_retry_rekeys_real_session_identity(tmp_path, monkeypatch):
    from hermes_cli import profile_identity, profiles
    from hermes_state import SessionDB
    import hermes_constants
    root = tmp_path / 'home'
    renamed = root / 'profiles' / 'new-name'
    renamed.mkdir(parents=True)
    monkeypatch.setenv('HERMES_HOME', str(root))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(hermes_constants, 'get_default_hermes_root', lambda: root)
    monkeypatch.setattr(profiles, 'get_profile_dir', lambda name: root / 'profiles' / name)
    monkeypatch.setattr(profile_identity, '_migrate_checkpoint_identity', lambda *args: True)
    db = SessionDB(root / 'state.db')
    try:
        db.create_session('terminal-rename', 'cli', profile_name='old-name')
        assert profile_identity.migrate_profile_identity('old-name', 'new-name')
        assert db.get_session('terminal-rename')['profile_name'] == 'new-name'
    finally:
        db.close()
