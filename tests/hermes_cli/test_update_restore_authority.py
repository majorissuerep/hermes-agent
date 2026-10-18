"""Maintenance must not replace the database behind a live authority."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import threading



def test_restore_refuses_live_terminal_without_changing_data(tmp_path):
    from hermes_cli.update_cmd_maint import _restore_state_db_from_snapshot
    from hermes_state.runtime_ownership import ProfileOwnership

    home = tmp_path / "state"
    home.mkdir()
    destination, source = home / "state.db", tmp_path / "snapshot.db"
    for path, value in ((destination, "current"), (source, "snapshot")):
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE marker(value TEXT)")
            db.execute("INSERT INTO marker VALUES(?)", (value,))
            db.commit()
    before = destination.read_bytes(), source.read_bytes()
    owner = ProfileOwnership()
    owner.reserve([home])
    try:
        assert not _restore_state_db_from_snapshot(destination, source)
        assert (destination.read_bytes(), source.read_bytes()) == before
    finally:
        owner.close()
    assert _restore_state_db_from_snapshot(destination, source)
    with closing(sqlite3.connect(destination)) as db:
        assert db.execute("SELECT value FROM marker").fetchall() == [("snapshot",)]
    assert source.read_bytes() == before[1]


def test_corrupt_epoch_refuses_profile_sweep_with_separate_recovery(tmp_path, monkeypatch, capsys):
    from hermes_cli import backup, update_cmd
    from hermes_cli.session_recovery import recover_session_database
    from hermes_state import SessionDB

    homes = [tmp_path / 'default', tmp_path / 'sibling']
    originals = {}
    snapshots = {}
    for home in homes:
        home.mkdir()
        with SessionDB(db_path=home / 'state.db') as db:
            db.create_session('retained', 'cli')
            db.append_message('retained', 'user', 'snapshot transcript')
        snapshot = backup.create_quick_snapshot(hermes_home=home)
        snapshots[home] = home / 'state-snapshots' / snapshot / 'state.db'
        (home / 'state.db').write_bytes(b'unreadable canonical runtime epoch')
        originals[home] = (home / 'state.db').read_bytes(), snapshots[home].read_bytes()
    monkeypatch.setattr(update_cmd, 'get_hermes_home', lambda: homes[0])
    monkeypatch.setattr(backup, '_sibling_profile_homes', lambda home: [('work', homes[1])])
    update_cmd._verify_and_restore_state_dbs_post_update()
    output = capsys.readouterr().out
    for home in homes:
        assert ((home / 'state.db').read_bytes(), snapshots[home].read_bytes()) == originals[home]
    assert 'Auto-restored' not in output
    assert 'separate' in output and 'hermes sessions recover' in output
    assert 'restored copy also failed integrity' not in output
    for home in homes:
        report = recover_session_database(snapshots[home], home / 'recovered.db')
        assert report['verified'] and report['source_unchanged']
        with closing(sqlite3.connect(home / 'recovered.db')) as db:
            assert db.execute('SELECT content FROM messages').fetchall() == [('snapshot transcript',)]
        assert (home / 'state.db').read_bytes() == originals[home][0]


def test_update_restore_excludes_startup_until_publication(tmp_path, monkeypatch):
    import subprocess
    import sys
    from hermes_state.runtime_ownership import ProfileOwnership
    from hermes_cli import backup
    from hermes_cli.update_cmd_maint import _restore_state_db_from_snapshot

    home = tmp_path / 'state'
    home.mkdir()
    source, destination = tmp_path / 'snapshot.db', home / 'state.db'
    for path, value in ((source, 'snapshot'), (destination, 'current')):
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE marker(value TEXT)')
            db.execute('INSERT INTO marker VALUES(?)', (value,))
            db.commit()
    before = destination.read_bytes(), source.read_bytes()
    owner = ProfileOwnership()
    owner.reserve([home])
    try:
        assert not (home / 'gateway.pid').exists()
        assert not _restore_state_db_from_snapshot(destination, source)
        assert (destination.read_bytes(), source.read_bytes()) == before
    finally:
        owner.close()

    # Pause only the actual database publication, with maintenance already held.
    arrived, release = threading.Event(), threading.Event()
    from hermes_cli import backup_restore
    publish = backup_restore._restore_db_pages
    def barrier(*args):
        arrived.set()
        assert release.wait(30)
        return publish(*args)
    monkeypatch.setattr(backup_restore, '_restore_db_pages', barrier)
    results = []
    thread = threading.Thread(target=lambda: results.append(_restore_state_db_from_snapshot(destination, source)))
    thread.start()
    try:
        assert arrived.wait(20)
        root = Path(__file__).resolve().parents[2]
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'TZ', 'TIRITH_ENABLED') if k in os.environ}
        env.update(HOME=str(tmp_path / 'user'), HERMES_HOME=str(home), PYTHONPATH=str(root), HERMES_ALLOW_NO_VAULT='1')
        contender = subprocess.run([sys.executable, '-c',
            "from pathlib import Path; from hermes_state.runtime_ownership import ProfileOwnership; "
            "from hermes_constants import get_hermes_home; owner=ProfileOwnership(); owner.reserve([get_hermes_home()])"],
            cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding='utf-8', timeout=25, check=False)
        assert contender.returncode != 0 and 'busy' in contender.stdout + contender.stderr, contender
        assert (destination.read_bytes(), source.read_bytes()) == before
    finally:
        release.set()
        thread.join(timeout=30)
    assert not thread.is_alive() and results == [True]
    with closing(sqlite3.connect(destination)) as db:
        assert db.execute('SELECT value FROM marker').fetchall() == [('snapshot',)]
    assert source.read_bytes() == before[1]
    print(json.dumps({'startup_first_refused': True, 'maintenance_first_refused': True,
                      'snapshot_unchanged': True}), flush=True)
