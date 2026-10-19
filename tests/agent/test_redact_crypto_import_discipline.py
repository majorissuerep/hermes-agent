"""Queued log redaction must preserve the updater's metadata-only import boundary."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.platforms("linux", "macos")
def test_vaultless_update_queued_redaction_does_not_load_crypto(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("secrets:\n  bitwarden:\n    enabled: true\n    project_id: test-project\n")
    code = '''
import io, logging, logging.handlers, queue, sys
sys.argv = ['hermes', 'update', '--check']
import hermes_cli.main as hermes_main
from hermes_cli import update_cmd
update_cmd._cmd_update_check = lambda **kwargs: 0
hermes_main.main()
from agent.redact import RedactingFormatter, register_vault_redaction_value
secret = 'opaque-queued-redaction-fixture-secret'
register_vault_redaction_value(secret)
output = io.StringIO()
handler = logging.StreamHandler(output)
handler.setFormatter(RedactingFormatter('%(message)s'))
pending = queue.Queue()
listener = logging.handlers.QueueListener(pending, handler)
logger = logging.getLogger('metadata-only-redaction-contract')
logger.propagate = False
logger.setLevel(logging.INFO)
logger.addHandler(logging.handlers.QueueHandler(pending))
listener.start()
try:
    logger.info('queued secret: %s', secret)
finally:
    listener.stop()
assert secret not in output.getvalue(), output.getvalue()
assert 'redacted' in output.getvalue(), output.getvalue()
loaded = [name for name in sys.modules if name == 'cryptography' or name.startswith('cryptography.')]
assert not loaded, loaded
print('QUEUED_REDACTION_OK_NO_CRYPTO')
'''
    result = subprocess.run([sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "HERMES_HOME": str(home), "BWS_ACCESS_TOKEN": ""},
        text=True, capture_output=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "QUEUED_REDACTION_OK_NO_CRYPTO" in result.stdout
