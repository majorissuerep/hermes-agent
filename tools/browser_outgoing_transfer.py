"""One captured outgoing target's bounded profile transfers; never creates/reconnects it."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hermes_platform import remote_capacity, remote_profile
from hermes_platform.client_capture import run_client
from hermes_security import browser_profile_archive as archive, browser_profile_transport as transport
from tools.browser_outgoing_state import OutgoingProfileError

TIMEOUT = 120


def client(lease, argv, *, payload=None, input_source=None, output_sink=None, maximum=2048):
    """Use the captured context, custody, exact client prefix and native generation once."""
    from tools import browser_terminal as terminal
    from tools.environments import streams

    def invoke():
        lease.custody.check()
        with terminal.captured_execution(lease.home, lease.backend, lease.env, lease.runtime):
            command = streams.remote_command(lease.env, lease.runtime.guarded(argv), interactive=True,
                                              expected_prefix=lease.runtime.client_identity)
            if command is None:
                raise OutgoingProfileError("Original backend cannot stream browser profiles; every copy retained")
            code, output, error = run_client(command, env=lease.client_env, timeout=TIMEOUT,
                                             payload=payload, input_source=input_source,
                                             output_sink=output_sink, max_output=maximum)
        lease.custody.check()
        if code != 0:
            if b"protected browser continuity requires Python 3.11" in error or b"python3: not found" in error:
                raise OutgoingProfileError(
                    f"Selected {lease.backend} target requires Python 3.11 or later for protected browser continuity; "
                    "install that prerequisite explicitly on the target. No source files were retired")
            detail = "busy or unknown browser owner" if b"owner" in error or b"busy" in error else "transfer/admission unavailable"
            raise OutgoingProfileError(f"Original outgoing browser profile {detail}; recovery state retained (exit {code})")
        return output

    return lease.context.copy().run(invoke)


def capacity(lease):
    command, payload, identity = remote_capacity.command(lease.runtime.profile)
    facts = remote_capacity.result(client(lease, command, payload=payload))
    lease.capacity_source_sha256 = identity
    return facts


def docker_identity(lease):
    """Public daemon fact through the SAME original local client environment; no target files."""
    lease.custody.check()
    code, output, _error = run_client(remote_capacity.docker_daemon_command(lease.env._docker_exe),
                                      env=lease.client_env, timeout=15, max_output=2048)
    lease.custody.check()
    if code:
        raise OutgoingProfileError("Original Docker daemon identity is unavailable; browser state retained")
    return remote_capacity.daemon_identity(output)


def require_target_capacity(lease, needed: int) -> None:
    facts = capacity(lease)
    if facts.available_bytes is None or facts.available_bytes < needed + 16 * 1024**2:
        raise OutgoingProfileError(
            f"The original target cannot admit {needed} bytes of protected browser state ({facts.reason}); retained")


def inspect(lease, *, path=None, stale=False) -> bool:
    binding = lease.binding if lease.bound and path is None else None
    command = remote_profile.command("inspect", path or lease.runtime.profile, maximum=2048, stale=stale, binding=binding)
    result = json.loads(client(lease, command))
    if not isinstance(result, dict) or type(result.get("empty")) is not bool:
        raise OutgoingProfileError("Original target returned no bounded profile state; retained")
    return result["empty"]


def claim(lease):
    client(lease, remote_profile.command("claim", lease.runtime.profile, maximum=2048, binding=lease.binding))
    lease.bound = True


def download(lease, ram: Path, *, path=None, stale=False):
    """Keep the exact raw tar identity for unchanged-source retirement; admit all members first."""
    maximum = transport.receive_bound(ram)
    target = ram / "transfer.tar"
    binding = lease.binding if lease.bound and path is None else None
    command = remote_profile.command("export", path or lease.runtime.profile, maximum=maximum, stale=stale, binding=binding)
    with target.open("w+b") as spool:
        client(lease, command, output_sink=spool, maximum=maximum)
        spool.flush()
        spool.seek(0)
        digest = hashlib.file_digest(spool, "sha256").hexdigest()
        spool.seek(0)
        restored = ram / "profile"
        restored.mkdir(mode=0o700)
        summary = transport.unpack(spool, restored)
    return restored, summary, digest, maximum


def prepare_restore(lease, ram: Path):
    """Authenticate/admit/pack before a recovery marker or target write is needed."""
    summary = archive.replay_archive(lease.home, lease.relative, custody=lease.custody,
                                      slot=lease.slot, digest=lease.digest)
    transport.require_capacity(ram, summary.bytes * 2 + summary.entries * 4096)
    require_target_capacity(lease, summary.bytes + summary.entries * 4096)
    if not inspect(lease):
        raise OutgoingProfileError("The original target has nonempty browser RAM; explicit repair is required")
    restored = ram / "restored"
    restored.mkdir(mode=0o700)
    archive.replay_archive(lease.home, lease.relative, custody=lease.custody, slot=lease.slot,
                            digest=lease.digest, target=restored)
    spool = ram / "restore.tar"
    with spool.open("w+b") as output:
        if transport.pack(restored, output) != summary:
            raise OutgoingProfileError("Authenticated browser restore staging changed; target retained")
        output.flush()
        output.seek(0)
    return summary, spool, restored


def restore_prepared(lease, ram: Path, summary, spool: Path, restored: Path):
    """Send only an authenticated staged profile; compare a fresh export before launch."""
    import shutil

    command = remote_profile.command("restore", lease.runtime.profile, maximum=spool.stat().st_size, binding=lease.binding)
    with spool.open("rb") as source:
        client(lease, command, input_source=source)
    shutil.rmtree(restored)  # verified durable archive remains the recovery source
    spool.unlink()
    checked, actual, _digest, _maximum = download(lease, ram)
    if actual != summary:
        raise OutgoingProfileError("Outgoing browser restore did not match its archive; target RAM retained for repair")
    return checked, summary


def retire(lease, digest: str, maximum: int, *, path=None, stale=False):
    """Retire only an unchanged quiescent copy already covered by verified durable ciphertext."""
    command = remote_profile.command("retire-legacy" if path else "retire", path or lease.runtime.profile,
                                     maximum=maximum, expected=digest, stale=stale,
                                     binding=lease.binding if lease.bound and path is None else None)
    client(lease, command)
