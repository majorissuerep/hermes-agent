"""Linux sandbox launcher: confine THIS process with Landlock + seccomp, then exec.

Run as ``python -I -S launcher.py <policy-json> -- argv...`` (Hermes passes this
file's source with ``-c``, captured at import) — stdlib only, no Hermes
imports, so the confined child never loads Hermes code or reads HERMES_HOME. The
restriction is inherited by every descendant and cannot be lifted, so a sandboxed
shell, its pipelines, background jobs and subagent commands all stay inside it.

Policy JSON (built by ``hermes_security.sandbox.policy``)::

    {"read": [paths], "write": [paths], "network": bool, "unix_sockets": bool}

``read`` grants read+execute, ``write`` grants every filesystem right. Paths that do
not exist are skipped. A regular-file path gets only file rights (Landlock rejects
directory rights on a file).

Why seccomp as well as Landlock: Landlock (through ABI 6) does not mediate
``connect()`` to pathname UNIX sockets, so a confined process could otherwise reach
the Docker socket or the systemd user bus and run code outside the sandbox. We block
creating AF_UNIX sockets (``socketpair`` stays allowed — libuv/node use it for
pipes), block io_uring (IORING_OP_SOCKET bypasses the ``socket`` syscall filter),
block the kernel keyring, and block AF_INET/AF_INET6 when the network is off.
Foreign-ABI syscalls (x32, i386 ``int 0x80``) are refused wholesale.

Exit status 126 with a ``hermes-sandbox:`` message on stderr means the sandbox
could not be applied; the command never runs unconfined.
"""

import ctypes
import errno
import json
import os
import struct
import sys

_SYS_CREATE_RULESET, _SYS_ADD_RULE, _SYS_RESTRICT_SELF = 444, 445, 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2

# Landlock filesystem rights, by the ABI that introduced them.
_FS_EXECUTE, _FS_WRITE_FILE, _FS_READ_FILE, _FS_READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
_FS_ABI1_ALL = (1 << 13) - 1        # EXECUTE .. MAKE_SYM
_FS_REFER = 1 << 13                  # ABI 2
_FS_TRUNCATE = 1 << 14               # ABI 3
_FS_IOCTL_DEV = 1 << 15              # ABI 5
_FILE_ONLY_RIGHTS = _FS_EXECUTE | _FS_WRITE_FILE | _FS_READ_FILE | _FS_TRUNCATE | _FS_IOCTL_DEV
_NET_BIND_TCP, _NET_CONNECT_TCP = 1 << 0, 1 << 1   # ABI 4
_SCOPE_ABSTRACT_UNIX, _SCOPE_SIGNAL = 1 << 0, 1 << 1  # ABI 6

_AF_UNIX, _AF_INET, _AF_INET6 = 1, 2, 10

# (audit arch, socket, keyctl, add_key, request_key, io_uring_setup/enter/register, x32 bit)
_ARCH = {
    "x86_64": (0xC000003E, 41, 250, 248, 249, (425, 426, 427), 0x40000000),
    "aarch64": (0xC00000B7, 198, 219, 217, 218, (425, 426, 427), None),
}

_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long


def _die(message):
    sys.stderr.write(f"hermes-sandbox: {message}\n")
    sys.stderr.flush()
    os._exit(126)


def landlock_abi():
    """Highest Landlock ABI the running kernel supports (0 = unavailable)."""
    abi = _libc.syscall(_SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(_CREATE_RULESET_VERSION))
    return abi if abi > 0 else 0


def _fs_rights(abi):
    rights = _FS_ABI1_ALL
    if abi >= 2:
        rights |= _FS_REFER
    if abi >= 3:
        rights |= _FS_TRUNCATE
    if abi >= 5:
        rights |= _FS_IOCTL_DEV
    return rights


def _add_path_rule(ruleset_fd, path, wanted, handled):
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return  # a grant for a path that does not exist (yet) confers nothing
    try:
        if not os.path.isdir(f"/proc/self/fd/{fd}"):
            wanted &= _FILE_ONLY_RIGHTS
        attr = struct.pack("=Qi", wanted & handled, fd)  # landlock_path_beneath_attr is packed
        buf = ctypes.create_string_buffer(attr, len(attr))
        if _libc.syscall(_SYS_ADD_RULE, ctypes.c_int(ruleset_fd), ctypes.c_int(_RULE_PATH_BENEATH),
                         buf, ctypes.c_uint32(0)) != 0:
            _die(f"landlock_add_rule({path}) failed: {os.strerror(ctypes.get_errno())}")
    finally:
        os.close(fd)


def apply_landlock(policy, abi):
    handled_fs = _fs_rights(abi)
    handled_net = (_NET_BIND_TCP | _NET_CONNECT_TCP) if abi >= 4 and not policy.get("network") else 0
    # Signal scoping: a confined process cannot signal Hermes or any other process
    # outside its own domain. Abstract UNIX sockets are also blocked by seccomp.
    scoped = (_SCOPE_ABSTRACT_UNIX | _SCOPE_SIGNAL) if abi >= 6 else 0
    if abi >= 6:
        attr = struct.pack("=QQQ", handled_fs, handled_net, scoped)
    elif abi >= 4:
        attr = struct.pack("=QQ", handled_fs, handled_net)
    else:
        attr = struct.pack("=Q", handled_fs)
    buf = ctypes.create_string_buffer(attr, len(attr))
    ruleset_fd = _libc.syscall(_SYS_CREATE_RULESET, buf, ctypes.c_size_t(len(attr)), ctypes.c_uint32(0))
    if ruleset_fd < 0:
        _die(f"landlock_create_ruleset failed: {os.strerror(ctypes.get_errno())}")
    ro = _FS_EXECUTE | _FS_READ_FILE | _FS_READ_DIR
    for path in policy.get("read", ()):
        _add_path_rule(ruleset_fd, path, ro, handled_fs)
    for path in policy.get("write", ()):
        _add_path_rule(ruleset_fd, path, handled_fs, handled_fs)
    if _libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        _die("prctl(PR_SET_NO_NEW_PRIVS) failed")
    if _libc.syscall(_SYS_RESTRICT_SELF, ctypes.c_int(ruleset_fd), ctypes.c_uint32(0)) != 0:
        _die(f"landlock_restrict_self failed: {os.strerror(ctypes.get_errno())}")
    os.close(ruleset_fd)


def _bpf(code, jt, jf, k):
    return struct.pack("=HBBI", code, jt, jf, k)


def seccomp_program(machine, *, network, unix_sockets):
    """Classic-BPF program bytes for this policy (pure; unit-testable)."""
    if machine not in _ARCH:
        raise ValueError(f"unsupported architecture for the sandbox: {machine}")
    arch, sys_socket, keyctl, add_key, request_key, io_uring, x32_bit = _ARCH[machine]
    ld_w_abs, jeq, jge, ret = 0x20, 0x15, 0x35, 0x06
    allow = 0x7FFF0000
    def deny(err):
        return 0x00050000 | err
    allowed_domains = [16]  # AF_NETLINK: glibc getaddrinfo/ifaddrs; no egress by itself
    if network:
        allowed_domains += [_AF_INET, _AF_INET6]
    if unix_sockets:
        allowed_domains.append(_AF_UNIX)
    blocked = (keyctl, add_key, request_key, *io_uring)

    prog = [_bpf(ld_w_abs, 0, 0, 4)]                       # seccomp_data.arch
    prog.append(_bpf(jeq, 1, 0, arch))
    prog.append(_bpf(ret, 0, 0, deny(errno.EPERM)))         # foreign ABI (i386 int 0x80)
    prog.append(_bpf(ld_w_abs, 0, 0, 0))                   # seccomp_data.nr
    if x32_bit is not None:
        prog.append(_bpf(jge, 0, 1, x32_bit))
        prog.append(_bpf(ret, 0, 0, deny(errno.EPERM)))     # x32 ABI
    for nr in blocked:
        prog.append(_bpf(jeq, 0, 1, nr))
        prog.append(_bpf(ret, 0, 0, deny(errno.EPERM)))
    prog.append(_bpf(jeq, 1, 0, sys_socket))
    prog.append(_bpf(ret, 0, 0, allow))                    # not socket(): allow
    prog.append(_bpf(ld_w_abs, 0, 0, 16))                  # args[0] low word = domain
    for domain in allowed_domains:
        prog.append(_bpf(jeq, 0, 1, domain))
        prog.append(_bpf(ret, 0, 0, allow))
    prog.append(_bpf(ret, 0, 0, deny(errno.EACCES)))
    return b"".join(prog)


def apply_seccomp(policy):
    program = seccomp_program(os.uname().machine, network=bool(policy.get("network")),
                              unix_sockets=bool(policy.get("unix_sockets")))
    count = len(program) // 8
    filt = ctypes.create_string_buffer(program, len(program))
    fprog = struct.pack("@HP", count, ctypes.addressof(filt))  # struct sock_fprog
    fbuf = ctypes.create_string_buffer(fprog, len(fprog))
    if _libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, fbuf, 0, 0) != 0:
        _die(f"seccomp filter failed: {os.strerror(ctypes.get_errno())}")


def main(argv):
    if len(argv) < 4 or argv[2] != "--":
        _die("usage: launcher.py <policy-json> -- command [args...]")
    policy = json.loads(argv[1])
    abi = landlock_abi()
    if abi < 1:
        _die("Landlock is unavailable on this kernel (needs Linux >= 5.13 with the landlock LSM enabled)")
    apply_landlock(policy, abi)
    apply_seccomp(policy)
    command = argv[3:]
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        sys.stderr.write(f"hermes-sandbox: cannot execute {command[0]}: {exc.strerror}\n")
        os._exit(127)


if __name__ == "__main__":
    main(sys.argv)
