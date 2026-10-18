"""The canonical store must not import gateway/ to compute its own identities (latent cycle)."""
import subprocess
import sys


def test_runtime_storage_modules_import_without_loading_gateway():
    probe = ("import sys, hermes_state, hermes_state.errors, hermes_state.local, hermes_state.local_lineage, "
             "hermes_state.legacy_receipts, hermes_state.mutation_retirement, hermes_state.session_constructor; "
             "print(sorted(m for m in sys.modules if m == 'gateway' or m.startswith('gateway.')))")
    out = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, check=True, timeout=30)
    assert out.stdout.strip() == '[]', out.stdout


def test_legacy_key_namespaces_decode_without_a_remote_store():
    from hermes_state import keys
    for key, profile in (('agent:main:local:dm:x', 'default'), ('agent:main~:local:dm:x', 'main'),
                         ('agent:work:local:dm:x', 'work'), ('agent::x', 'default'), ('cli:x', None), (None, None)):
        assert keys.profile_from_session_key(key) == profile
