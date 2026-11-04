"""Typed consumers and RAM-only staging for encrypted MCP media artifacts."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import tempfile

from tools.browser_media_custody import protected_media_workspace

_WORKSPACE: ContextVar[Path | None] = ContextVar("private_media_workspace", default=None)


def temporary_directory_options() -> dict:
    """Only a scoped private-media consumer redirects its subordinate temp files."""
    directory = _WORKSPACE.get()
    return {"dir": str(directory)} if directory is not None else {}


@contextmanager
def processing_workspace():
    with protected_media_workspace("mcp-media") as directory:
        token = _WORKSPACE.set(directory)
        try:
            yield directory
        finally:
            _WORKSPACE.reset(token)


def extract_document(data: bytes, path: str) -> str:
    from tools.read_extract import extract_document_bytes

    with processing_workspace():
        return extract_document_bytes(data, path)


def transcribe_owned(path, invoke):
    """The original remains encrypted; path-based codecs receive only a RAM copy."""
    from agent.file_safety import raise_if_model_read_blocked
    from hermes_security import artifacts
    from hermes_security.artifact_paths import media_kind

    raise_if_model_read_blocked(str(path))
    _, relative = artifacts._location(path)
    if media_kind(relative) != "audio":
        raise PermissionError("Only this profile's private audio artifacts may be transcribed")
    data = artifacts.read_bytes(path)
    with processing_workspace() as directory:
        with tempfile.TemporaryDirectory(prefix="audio-", dir=directory) as stage:
            source = Path(stage) / ("source" + Path(path).suffix)
            source.write_bytes(data)
            source.chmod(0o600)
            return invoke(str(source))
