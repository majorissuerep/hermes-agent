"""Small, profile-root-owned startup receipts; never persist child output or argv."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import time


def write_startup_receipt(*, status: str, pid: int, started: float, exit_code: int | None = None) -> Path | None:
    from hermes_constants import get_default_hermes_root
    from hermes_security import io
    from hermes_security.errors import VaultError
    from hermes_security.vault import _atomic_write

    path = Path(get_default_hermes_root()) / "runtime" / "session-host-startup.json"
    text = json.dumps({"status": status, "pid": pid, "exit_code": exit_code,
                       "elapsed_seconds": round(time.monotonic() - started, 3)})
    try:
        if io._home_for(path) is not None:
            io.write_text(path, text, purpose="state")
        else:
            # Vault-less test/pre-migration roots still get private files from creation.
            _atomic_write(path, text.encode("utf-8"), mode=0o600)
    except (OSError, VaultError) as exc:
        logging.getLogger(__name__).warning("Startup receipt unavailable (%s)", type(exc).__name__)
        return None
    return path
