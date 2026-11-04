"""Read-only M1 provenance heldout; a synthetic fixture subprocess, never actual Lean/Hermes."""
import json
import sys

import pytest

from tests.agent._formal_checker import install_checker


def _checkpoint():
    from agent.formal_logic_schema import parse_checkpoint
    return parse_checkpoint(json.dumps({'version': 1, 'atoms': {'P': 'public proposition'},
        'assumptions': [], 'hypotheses': [{'id': 'h', 'statement': 'P implies itself',
        'formula': ['implies', 'P', 'P'], 'from': []}], 'conclusion': 'h'}))


def _checker_body():
    from agent.formal_logic_lean import LEAN_COMMIT, LEAN_VERSION
    return f'''import json,re,sys
if '--version' in sys.argv:
 print('Lean (version {LEAN_VERSION}, commit {LEAN_COMMIT})')
 raise SystemExit(0)
source=sys.stdin.read()
if 'example : False' in source:
 print(json.dumps({{'severity':'error','data':'fixture rejects false canary'}}))
 raise SystemExit(1)
for name in re.findall(r'#print axioms (hermes_[a-z0-9_]+)',source):
 print(json.dumps({{'severity':'information','data':"'"+name+"' does not depend on any axioms"}}))
'''

def test_unowned_executable_cannot_qualify_as_pm_verified_lean(tmp_path):
    from agent.formal_logic_lean import LEAN_COMMIT, LEAN_VERSION, verify
    from agent.formal_logic_schema import parse_checkpoint
    compiler = tmp_path / 'arbitrary-unowned-compiler'
    compiler.write_text(f'''#!{sys.executable}
import json,re,sys
if '--version' in sys.argv:
 print('Lean (version {LEAN_VERSION}, commit {LEAN_COMMIT})')
 raise SystemExit(0)
source=sys.stdin.read()
if 'example : False' in source:
 print(json.dumps({{'severity':'error','data':'fixture rejected false canary'}}))
 raise SystemExit(1)
for name in re.findall(r'#print axioms (hermes_[a-z0-9_]+)',source):
 print(json.dumps({{'severity':'information','data':"'"+name+"' does not depend on any axioms"}}))
''')
    compiler.chmod(0o700)
    checkpoint = parse_checkpoint(json.dumps({'version':1,'atoms':{'P':'public fixture proposition'},
        'assumptions':[],'hypotheses':[{'id':'h','statement':'P implies itself',
        'formula':['implies','P','P'],'from':[]}],'conclusion':'h'}))
    result = verify(checkpoint, str(compiler))
    assert result['status'] != 'logic_verified', 'An arbitrary unowned executable supplied the Lean certificate'


@pytest.mark.parametrize('explicit', [False, True])
def test_declared_pm_tree_and_matching_legacy_path_keep_the_existing_checker_usable(tmp_path, monkeypatch, explicit):
    from agent.formal_logic_lean import verify
    binary = install_checker(tmp_path, monkeypatch, _checker_body())
    result = verify(_checkpoint(), str(binary) if explicit else 'lean')
    assert result['status'] == 'logic_verified'
    assert result['proof_status'] == 'verified_finite_conditional_deductions'
    assert result['premise_truth'] == 'not_checked_by_kernel'
    assert result['checker']['invocations'] == 3
    toolchain = result['checker']['toolchain']
    assert toolchain['pm_verified'] and toolchain['archive_sha256']
    assert len(toolchain['tree_sha256']) == len(toolchain['binary_sha256']) == 64
    assert result['checker']['workspace'] == 'unit_directory_not_a_RAM_proof'


@pytest.mark.parametrize('changed', ['binary', 'standard-library', 'archive-pin'])
def test_changed_pm_payload_is_refused_before_any_executable_probe(tmp_path, monkeypatch, changed):
    from agent.formal_logic_lean import verify
    from pm import paths
    from pm.lock import Lockfile
    from pm.store import current_target

    marker = tmp_path / 'probe-must-not-run'
    binary = install_checker(tmp_path, monkeypatch,
        f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n' + _checker_body())
    if changed == 'binary':
        binary.write_text(binary.read_text() + '\n# payload drift\n')
    elif changed == 'standard-library':
        (binary.parent.parent / 'lib' / 'lean' / 'Init.olean').unlink()
    else:
        lock_path = tmp_path / 'repinned-lock.json'
        lock_path.write_bytes(paths.lockfile_path().read_bytes())
        lock = Lockfile(lock_path)
        artifacts = lock.pinned_artifacts('lean')
        artifacts[current_target()]['sha256'] = '0' * 64
        lock.set_pin('lean', '4.35.0', artifacts)
        lock.save()
        monkeypatch.setattr(paths, 'lockfile_path', lambda: lock_path)
    result = verify(_checkpoint())
    assert result['status'] == 'checker_unavailable'
    assert result['checker']['invocations'] == 0 and not marker.exists()


def test_explicit_foreign_path_is_not_silently_redirected_to_a_valid_pm_checker(tmp_path, monkeypatch):
    from agent.formal_logic_lean import verify
    marker = tmp_path / 'no-compiler-dispatched'
    install_checker(tmp_path, monkeypatch,
        f'from pathlib import Path\nPath({str(marker)!r}).touch()\n' + _checker_body())
    foreign = tmp_path / 'other-checker'
    foreign.write_text(_checker_body())
    foreign.chmod(0o700)
    result = verify(_checkpoint(), str(foreign))
    assert result['status'] == 'checker_unavailable' and result['checker']['invocations'] == 0
    assert not marker.exists()


def test_checker_modified_during_version_probe_cannot_qualify_later_calls(tmp_path, monkeypatch):
    from agent.formal_logic_lean import verify
    body = _checker_body().replace(" print('Lean", " from pathlib import Path\n Path(sys.argv[0]).write_text(Path(sys.argv[0]).read_text()+'\\n# changed')\n print('Lean", 1)
    install_checker(tmp_path, monkeypatch, body)
    result = verify(_checkpoint())
    assert result['status'] == 'checker_failed' and result['checker']['invocations'] == 1
    assert 'changed' in result['error']


def test_exit_zero_without_false_canary_diagnostics_never_certifies_a_pm_checker(tmp_path, monkeypatch):
    from agent.formal_logic_lean import LEAN_COMMIT, LEAN_VERSION, verify
    install_checker(tmp_path, monkeypatch, f'''import sys
print('Lean (version {LEAN_VERSION}, commit {LEAN_COMMIT})' if '--version' in sys.argv else '')
''')
    result = verify(_checkpoint())
    assert result['status'] == 'checker_failed' and 'false canary' in result['error']
    assert result['checker']['invocations'] == 2
