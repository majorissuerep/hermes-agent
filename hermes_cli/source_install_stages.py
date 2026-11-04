"""Fixed installer stages borrow fresh one-shot FDs from their supervising owner.

The shell holds only a private inherited control socket. It cannot choose a
program, interpreter, checkout, profile or working directory through this API.
The existing process supervisor owns the entire ladder, including stage execs.
"""
from __future__ import annotations

import array
import fcntl
from contextlib import contextmanager
import json
import os
import select
from pathlib import Path
import socket
import stat
import sys
import threading

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OWNER_FD_ENV = "__HERMES_INSTALL_OWNER_FD"
_ROOT = Path(__file__).resolve().parents[1]
_ORDER = {"dependencies": 0, "config": 1, "products": 2, "setup": 3}


def _stage_arguments(arguments):
    if not isinstance(arguments, list) or not arguments or arguments[0] not in _ORDER:
        raise ValueError("unknown installer stage")
    stage, options = arguments[0], arguments[1:]
    if stage != "dependencies" and options:
        raise ValueError("installer stage takes no options")
    if (len(options) > 4 or len(options) % 2 or options[::2] != ["--without"] * (len(options) // 2)
            or any(value not in {"agent-browser", "cua-driver"} for value in options[1::2])
            or len(set(options[1::2])) != len(options[1::2])):
        raise ValueError("invalid installer dependency options")
    return stage, options


def _stage_command(arguments):
    _stage_arguments(arguments)
    return [sys.executable, "-I", "-B", str(Path(__file__).resolve()), *arguments]


def _stdio(descriptors):
    if len(descriptors) != 3:
        raise ValueError("installer stage requires three stdio descriptors")
    for index, descriptor in enumerate(descriptors):
        mode = os.fstat(descriptor).st_mode
        access = fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
        if (not any(check(mode) for check in (stat.S_ISREG, stat.S_ISCHR, stat.S_ISFIFO, stat.S_ISSOCK))
                or access == (os.O_WRONLY if index == 0 else os.O_RDONLY)):
            raise ValueError("invalid installer stage stdio")
    return dict(zip(("stdin", "stdout", "stderr"), descriptors))


def _run_stage(arguments, descriptors, reply, home, authority, env, stop):
    from hermes_platform.managed_supervision import SupervisedChild
    from hermes_security.handoff import KEY_FD_ENV, prepare_child_key
    from hermes_security.session_authority import ENV_NAME

    command = _stage_command(arguments)  # The owner selects AND executes; no executable reaches the client.
    stage_env = {**env, "HERMES_HOME": str(home), "HERMES_SESSION_ID": authority.session_id,
                 **authority.environment()}
    for name in (OWNER_FD_ENV, KEY_FD_ENV, "HERMES_MASTER_PASSWORD", "HERMES_VAULT_PRIVATE_KEY"):
        stage_env.pop(name, None)
    handoff = prepare_child_key(home, source_grant=stage_env[ENV_NAME])
    if handoff is None:
        raise RuntimeError("installer owner is locked")
    try:
        from hermes_cli.source_install_log import LOG_FD_ENV
        inherit_fds = (handoff[0],)
        if log_fd := stage_env.get(LOG_FD_ENV):
            inherit_fds += (int(log_fd),)
        stage_env.update(handoff[1])
        child = SupervisedChild(command, env=stage_env, cwd=_ROOT, inherit_fds=inherit_fds,
                                **_stdio(descriptors))
    finally:
        os.close(handoff[0])
    try:
        while (result := child.poll()) is None:
            if stop.is_set() or select.select([reply], [], [], 0.1)[0]:
                # EOF means the requesting helper died; additional input is invalid too.
                child.cancel()
                return 130
        return result
    except BaseException:
        child.cancel()
        raise


def _serve_request(data, ancillary, flags, home, authority, env, stop, previous):
    descriptors = array.array("i")
    reply = None
    try:
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                descriptors.frombytes(payload[:len(payload) - len(payload) % descriptors.itemsize])
        if flags or len(descriptors) != 4:
            raise ValueError("installer stage requires a reply socket and stdio")
        reply = socket.socket(fileno=descriptors[0])
        descriptors.pop(0)
        if reply.family != socket.AF_UNIX or reply.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM:
            raise ValueError("installer reply must use a private Unix socket")
        arguments = json.loads(data)
        stage, _ = _stage_arguments(arguments)
        if _ORDER[stage] < previous:
            raise ValueError("installer stage order exceeded")
        previous = _ORDER[stage]
        result = _run_stage(arguments, descriptors, reply, home, authority, env, stop)
        reply.sendall(json.dumps({"returncode": result}).encode() + b"\n")
    except (OSError, RuntimeError, ValueError, TypeError):
        if reply is not None:
            try:
                reply.sendall(b'{"error":"installer stage authority refused"}\n')
            except OSError:
                pass  # The requesting helper exited; its owned stage was already cancelled.
    finally:
        if reply is not None:
            reply.close()
        for descriptor in descriptors:
            os.close(descriptor)
    return previous


def _serve(connection, home, authority, env, stop):
    previous = -1
    connection.settimeout(0.1)
    for _ in range(16):
        while not stop.is_set():
            try:
                data, ancillary, flags, _ = connection.recvmsg(2048, socket.CMSG_SPACE(4 * array.array("i").itemsize))
                break
            except TimeoutError:
                continue
            except OSError:
                return
        else:
            return
        previous = _serve_request(data, ancillary, flags, home, authority, env, stop, previous)
    connection.close()


@contextmanager
def _stage_owner(home, authority, env):
    from agent.memory_provider import spawn_context_thread

    owner, child = socket.socketpair(type=socket.SOCK_DGRAM)
    stop = threading.Event()
    thread = spawn_context_thread(_serve, args=(owner, home, authority, env, stop),
                                  name="encrypted-install-stages", daemon=True)
    env[OWNER_FD_ENV] = str(child.fileno())
    thread.start()
    try:
        yield child.fileno()
    finally:
        stop.set()
        child.close()
        owner.close()
        env.pop(OWNER_FD_ENV, None)
        thread.join(timeout=20)
        if thread.is_alive():
            raise TimeoutError("installer stage authority did not stop")


def run_owned_install(command, home, authority, env, pass_fds, *, stages):
    from hermes_platform.managed_supervision import SupervisedChild
    from hermes_security.handoff import KEY_FD_ENV, prepare_child_key
    from hermes_security.session_authority import ENV_NAME

    env.pop(KEY_FD_ENV, None)
    for name in ("HERMES_MASTER_PASSWORD", "HERMES_VAULT_PRIVATE_KEY"):
        env.pop(name, None)
    if stages:
        with _stage_owner(home, authority, env) as descriptor:
            return _wait_owned(SupervisedChild(command, env=env, inherit_fds=(*pass_fds, descriptor)))
    handoff = prepare_child_key(home, source_grant=authority.environment()[ENV_NAME])
    if handoff is None:
        raise RuntimeError("installer owner is locked")
    try:
        env.update(handoff[1])
        child = SupervisedChild(command, env=env, inherit_fds=(*pass_fds, handoff[0]))
    finally:
        os.close(handoff[0])
    return _wait_owned(child)


def _wait_owned(child):
    try:
        return child.wait(timeout=7200)
    except BaseException:
        child.cancel()
        raise


def request_stage(arguments):
    """Request a fixed owner-executed stage; replies never contain keys or descriptors."""
    _stage_arguments(arguments)
    raw = os.environ.pop(OWNER_FD_ENV, "")
    if not raw.isdigit() or int(raw) < 3:
        raise RuntimeError("installer stage requires its owning kernel descriptor")
    reply, sender = socket.socketpair()
    try:
        with socket.socket(fileno=int(raw)) as connection:
            connection.settimeout(10)
            descriptors = array.array("i", [sender.fileno(), 0, 1, 2])
            connection.sendmsg([json.dumps(arguments).encode()],
                               [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptors)])
        sender.close()
        reply.settimeout(7200)
        with reply.makefile("rb") as reader:
            data = reader.readline(513)
        if len(data) > 512 or not data.endswith(b"\n"):
            raise RuntimeError("invalid installer stage receipt")
        result = json.loads(data)
        code = result.get("returncode")
        if set(result) != {"returncode"} or type(code) is not int or not 0 <= code <= 255:
            raise RuntimeError("installer stage authority refused")
        return code
    finally:
        sender.close()
        reply.close()


def execute_stage(arguments):
    stage, options = _stage_arguments(arguments)
    from pm.state_io import prepare_owner

    prepare_owner()  # activate crypto if needed, consume this stage's FD, retain exact lineage
    if stage == "dependencies":
        from pm.cli import main
        return main(["install", *options])
    if stage == "config":
        from hermes_cli.source_install_seed import seed_encrypted_install_state
        from hermes_constants import get_hermes_home
        seed_encrypted_install_state(get_hermes_home())
        return 0
    if stage == "products":
        from hermes_cli.source_completion import main
        return main(["--source", str(_ROOT)])
    from pm.environments import activate_dependencies
    activate_dependencies(_ROOT)
    sys.argv = ["hermes", "setup"]
    from hermes_cli.main import main
    return main()


if __name__ == "__main__":
    raise SystemExit(execute_stage(sys.argv[1:]))
