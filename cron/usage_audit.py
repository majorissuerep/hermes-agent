"""Append usage records in the same protected format as canonical migration."""
from __future__ import annotations

import json
from pathlib import Path

from hermes_security import frames, io


def append_record(path: Path, record: dict) -> None:
    """Seal each record in vaulted homes; retain pre-migration JSONL compatibility.

    Migration frames each legacy line with purpose ``transcript``. New records
    use that same format, with JSON carrying its own complete record boundary.
    An existing vaulted plaintext/corrupt stream is refused by the frame writer;
    explicit secure-vault repair owns backfill, never this best-effort logger.
    """
    payload = json.dumps(record, ensure_ascii=False).encode("utf-8")
    if io._home_for(path) is not None:
        frames.append(path, payload, purpose="transcript")
    else:
        with open(path, "ab") as stream:
            stream.write(payload + b"\n")
