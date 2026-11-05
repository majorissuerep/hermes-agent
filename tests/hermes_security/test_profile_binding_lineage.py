"""Real one-shot FD adoption keeps ordinary logical profiles separate from joint guards."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest_plugins = ["tests.hermes_security.test_profile_resource_binding"]
pytestmark = pytest.mark.platforms("posix")

_WORKER = r'''
import json, os, sys
from pathlib import Path
from hermes_security.handoff import adopt_inherited_key, KEY_FD_ENV
from hermes_security.profile_binding import capture
from hermes_security.session_authority import capture_inherited_authority
assert adopt_inherited_key()
a, b, owner = map(Path, sys.argv[1:4])
def works(home):
    try:
        return len(capture(home).derive_key("logical-profile-lineage-contract")) == 32
    except (RuntimeError, OSError):
        return False
if sys.argv[4] == "human":
    print(json.dumps({"human_no_session_FD": works(a)}), flush=True)
else:
    original = capture(a)
    wrong_before = works(b)
    os.environ[KEY_FD_ENV] = sys.argv[5]
    assert adopt_inherited_key()
    print("READY", flush=True)
    sys.stdin.readline()
    def old_works():
        try:
            return len(original.derive_key("original-A-after-B-closure")) == 32
        except (RuntimeError, OSError):
            return False
    first = old_works()
    fresh = works(a)
    wrong_after = works(b)
    try:
        capture_inherited_authority(owner).validate()
        joint = True
    except (RuntimeError, OSError):
        joint = False
    print(json.dumps({"original_A_before": first, "fresh_A": fresh,
        "wrong_B_before_adoption": wrong_before, "closed_B": wrong_after,
        "joint_all_member_guard": joint, "original_A_after": old_works()}), flush=True)
'''


def _environment(home, addition):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("HERMES_") and not key.endswith(("_API_KEY", "_TOKEN"))}
    return {**env, "HOME": str(home.parent.parent.parent), "HERMES_HOME": str(home), **addition}


def _child(args, env, descriptors):
    try:
        return subprocess.Popen([sys.executable, "-c", _WORKER, *map(str, args)],
            cwd=Path(__file__).resolve().parents[2], env=env, pass_fds=tuple(descriptors),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def test_original_and_fresh_A_survive_B_close_but_joint_and_wrong_home_refuse(machine):
    from hermes_security.handoff import prepare_child_key
    from hermes_security.session_authority import ENV_NAME, start_session_authority
    from tests.hermes_security.test_profile_resource_binding import _profile

    a, b = _profile("a"), _profile("b")
    with ExitStack() as owners:
        source_a = start_session_authority(a, "native-profile-A")
        source_b = start_session_authority(b, "native-profile-B")
        owners.callback(source_a.close)
        owners.callback(source_b.close)
        first = prepare_child_key(a, source_grant=source_a.environment()[ENV_NAME])
        second = prepare_child_key(b, source_grant=source_b.environment()[ENV_NAME])
        assert first is not None and second is not None
        child = _child([a, b, machine, "scoped", second[0]], _environment(a, first[1]), (first[0], second[0]))
        try:
            # B must be adopted while live; its later closure is the actual falsifier.
            from selectors import DefaultSelector, EVENT_READ
            with DefaultSelector() as ready:
                ready.register(child.stdout, EVENT_READ)
                assert ready.select(timeout=10), "bounded native FD helper startup"
            assert child.stdout.readline().strip() == "READY"
            source_b.close()
            output, error = child.communicate("probe\n", timeout=20)
            assert child.returncode == 0, error
            observed = json.loads(output)
            assert observed == {"original_A_before": True, "fresh_A": True,
                "wrong_B_before_adoption": False, "closed_B": False,
                "joint_all_member_guard": False, "original_A_after": True}
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)


def test_actual_human_FD_bridge_needs_no_session_grant(machine):
    from hermes_security.handoff import prepare_child_key
    from tests.hermes_security.test_profile_resource_binding import _profile

    a = _profile("human-child")
    handoff = prepare_child_key(a, retain_session=False)
    assert handoff is not None
    child = _child([a, a, machine, "human"], _environment(a, handoff[1]), (handoff[0],))
    try:
        output, error = child.communicate(timeout=20)
        assert child.returncode == 0, error
        assert json.loads(output) == {"human_no_session_FD": True}
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)
