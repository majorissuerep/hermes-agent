"""Early public CLI forwarding for operation-only grants; this process holds no key."""
from __future__ import annotations

import base64
import os
import select
import signal
import sys
import threading
import time


def _read_input():
    if sys.stdin.isatty():
        return b""
    deadline, chunks, size = time.monotonic() + 5, [], 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin], [], [], max(0, remaining))[0]:
            raise ValueError("native stdin did not reach EOF before its deadline")
        chunk = os.read(sys.stdin.fileno(), 16384)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > 524288:
            raise ValueError("native stdin exceeds the bounded input limit")
        chunks.append(chunk)


def _cancel(_signal, _frame):
    raise KeyboardInterrupt


def main(arguments):
    from hermes_constants import get_hermes_home
    from hermes_security.session_authority import ENV_NAME, operation_request, SessionAuthorityError, LegacySessionAuthorityError

    home, raw = get_hermes_home().resolve(), os.environ[ENV_NAME]
    job = None
    previous = signal.signal(signal.SIGTERM, _cancel)
    try:
        data = _read_input()
        response = operation_request(home, raw, {"op": "native.start", "arguments": arguments,
                                                "stdin": base64.b64encode(data).decode("ascii")})
        if response.get("ok") is not True:
            raise ValueError(response.get("native_error", "native command refused"))
        job = response["job"]
        pause = threading.Event()
        while True:
            response = operation_request(home, raw, {"op": "native.poll", "job": job})
            if response.get("ok") is not True:
                raise ValueError(response.get("native_error", "native command unavailable"))
            for chunk in response.get("chunks", []):
                stream = sys.stdout if chunk["stream"] == "stdout" else sys.stderr
                stream.buffer.write(base64.b64decode(chunk["data"], validate=True))
                stream.flush()
            if response.get("done"):
                job = None
                rc = response.get("returncode")
                return rc if isinstance(rc, int) and rc >= 0 else (128 - rc if isinstance(rc, int) else 2)
            pause.wait(0.05)
    except KeyboardInterrupt:
        return 130
    except LegacySessionAuthorityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Hermes session command refused: {exc}.", file=sys.stderr)
        return 2
    except (OSError, SessionAuthorityError):
        print("Hermes session command refused; use the owning terminal or a supported headless command.", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous)
        if job is not None:
            try:
                operation_request(home, raw, {"op": "native.cancel", "job": job})
            except (OSError, ValueError, SessionAuthorityError):
                pass  # The owner's idle deadline also revokes an abandoned command.
