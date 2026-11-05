"""Existing file-tool recovery for explicit own-profile encrypted output paths."""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from agent.file_safety import get_model_read_block_error, get_nt_namespace_error
from agent.redact import redact_sensitive_text
from hermes_security import artifacts
from hermes_security.artifact_paths import is_artifact_directory, is_artifact_relative_path, is_image_relative_path, media_kind
from tools.file_operations import IMAGE_EXTENSIONS, ShellFileOperations
from tools.file_operations_common import ReadResult, SearchMatch, SearchResult

_SOURCE = "profile-owned private artifact (host; not the task execution backend)"
_SCAN_BYTES = 64 * 1024 * 1024
_SCAN_FILES = 1000
_VIDEO_RECOVERY = ("Private video is binary. Authenticate and recover it explicitly: hermes secure-vault export "
                   "--output /outside-home/recovery.tar (whole-profile plaintext archive).")


def _admit(path):
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() or not artifacts.is_artifact_path(candidate):
        return None
    block = get_model_read_block_error(str(candidate))
    if block:
        raise PermissionError(block)
    selected, candidate = artifacts._binding(candidate)
    # Check scope even when a caller requests only filenames or a binary redirect.
    if candidate.is_dir():
        try:
            relative = candidate.relative_to(selected)
        except ValueError as exc:
            raise PermissionError("Private artifact belongs to a different profile") from exc
        if not is_artifact_directory(relative):
            raise PermissionError("Not an admitted private output directory")
        artifacts._bound_custody(selected)
    else:
        artifacts._location(candidate)
    return candidate


def _require_video_file(candidate):
    """Check owner, existence and original custody; metadata alone cannot authenticate video content."""
    home, relative = artifacts._location(candidate)
    custody = artifacts._bound_custody(home)
    with artifacts._parent(home, relative) as parent:
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise PermissionError("Private video must be an owned single-link regular file")
            if info.st_size > artifacts._wire_bound(relative):
                raise ValueError("Private video exceeds its bounded storage format")
            custody.check()
        finally:
            os.close(fd)


def read_file(path, offset, limit, max_chars):
    """None means ordinary backend file; recognized artifact failures never fall back."""
    nt_error = get_nt_namespace_error(path, verb="Read")
    if nt_error:
        from tools.registry import tool_error

        return tool_error(nt_error)
    candidate = _admit(path)
    if candidate is None:
        return None
    relative = artifacts._location(candidate)[1]
    if media_kind(relative) == "videos":  # never decrypt up to 200 MiB only to refuse it as text
        _require_video_file(candidate)
        return json.dumps({**ReadResult(is_binary=True, error=_VIDEO_RECOVERY).to_dict(), "source": _SOURCE})
    raw = artifacts.read_bytes(candidate)
    extracted = False
    if candidate.suffix.lower() in IMAGE_EXTENSIONS:
        result = ShellFileOperations._image_redirect_result(len(raw)).to_dict()
    elif is_image_relative_path(relative):
        result = ReadResult(is_binary=True, error="Private browser artifact is binary; use vision_analyze.").to_dict()
    elif media_kind(relative) in {"audio", "videos"}:
        result = ReadResult(is_binary=True, file_size=len(raw),
                            error="Encrypted media retained. Explicit offline recovery: hermes secure-vault export "
                                  "--output /outside-home/recovery.tar (whole-profile plaintext archive).").to_dict()
    else:
        from tools.tool_output_limits import get_max_line_length

        text, extracted = _document_text(candidate, relative, raw)
        if text is None:
            return json.dumps({**ReadResult(is_binary=True, file_size=len(raw),
                               error="Encrypted binary document retained; no text extractor supports this format.").to_dict(),
                               "source": _SOURCE})
        lines = text.splitlines()
        selected = lines[offset - 1:offset - 1 + limit]
        line_cap = get_max_line_length()
        rendered, used, clipped = [], 0, False
        for number, line in enumerate(selected, offset):
            shown = line if len(line) <= line_cap else line[:line_cap] + "... [truncated]"
            row = f"{number}|{shown}"
            if rendered and used + len(row) + 1 > max_chars:
                break
            rendered.append(row[:max_chars])
            used += len(row) + 1
            clipped |= len(line) > line_cap
        end = offset + len(rendered) - 1
        result = ReadResult(content=redact_sensitive_text("\n".join(rendered), file_read=True),
                            total_lines=len(lines), file_size=len(raw), truncated=end < len(lines),
                            truncated_lines=clipped or None).to_dict()
        if result["truncated"]:
            result.update(next_offset=end + 1, hint=f"Use offset={end + 1} to continue reading.")
    result["source"] = _SOURCE
    if extracted:
        result["extracted_document"] = True
    return json.dumps(result, ensure_ascii=False)


def _document_text(candidate, relative, raw):
    if media_kind(relative) != "documents":
        return raw.decode("utf-8-sig", errors="replace"), False
    from tools.read_extract import ANYDOC_EXTENSIONS, EXTRACTABLE_EXTENSIONS

    if candidate.suffix.lower() in ANYDOC_EXTENSIONS | EXTRACTABLE_EXTENSIONS:
        from tools.media_artifacts import extract_document
        from tools.read_extract import ExtractionError

        try:
            return extract_document(raw, str(candidate)), True
        except ExtractionError as exc:
            raise ValueError(f"Private document extraction unavailable: {exc}") from exc
    try:
        text = raw.decode("utf-8-sig")
        return (None if "\x00" in text else text), False
    except UnicodeDecodeError:
        return None, False


def _candidates(root, file_glob, order):
    if root.is_symlink():
        raise PermissionError("Private artifact symlinks are refused")
    paths = [root] if root.is_file() else root.rglob("*")
    selected, _ = artifacts._binding(root)
    result = []
    for scanned, item in enumerate(paths):
        if scanned >= 10_000:
            raise ValueError("Private artifact search walk exceeded its bound; narrow the path")
        if item.is_symlink():
            raise PermissionError("Private artifact symlinks are refused")
        if item.is_file() and is_artifact_relative_path(item.relative_to(selected)) and (
                              not file_glob or fnmatch.fnmatch(item.name, file_glob)
                               or fnmatch.fnmatch(str(item.relative_to(root)), file_glob)):
            result.append(item)
            if len(result) > _SCAN_FILES:
                raise ValueError("Private artifact search exceeds 1000 files; narrow the path or file_glob")
    return sorted(result, key=(lambda p: (-p.stat().st_mtime, str(p))) if order == "modified" else str)


def _run_rg(text, pattern, options, timeout):
    """Bounded native regex engine; private input travels only through anonymous pipes."""
    from hermes_platform.resolver import locate_command
    from tools.file_operations_search import _bundled_rg, _pattern_has_regex_newline

    binary = _bundled_rg() or next(iter(locate_command("rg").command), None)
    if binary is None:
        raise RuntimeError("Private artifact search requires the installed ripgrep tool")
    argv = [binary, *options, "--regexp", pattern]
    if _pattern_has_regex_newline(pattern):
        argv.append("--multiline")
    proc = subprocess.run([*argv, "--", "-"], input=text, capture_output=True, timeout=timeout,
                          env={"LANG": "C.UTF-8", "PATH": os.defpath}, check=False)
    if proc.returncode not in (0, 1):
        raise ValueError("Private artifact search failed: invalid or unsupported search pattern")
    return proc


def _matching_lines(text, pattern, context, fetch, timeout):
    proc = _run_rg(text, pattern, ["--json", "--max-count", str(fetch), "--context", str(context),
                                  "--max-columns", "2000", "--max-columns-preview"], timeout)
    matches = []
    for encoded in proc.stdout.splitlines():
        row = json.loads(encoded)
        if row.get("type") not in {"match", "context"}:
            continue
        data = row["data"]
        content = data["lines"].get("text", "").rstrip("\n")
        matches.append((data["line_number"], content, row["type"] == "match"))
    return matches


def _search_text(candidate, relative, raw):
    if media_kind(relative) != "documents":
        return raw
    text, _ = _document_text(candidate, relative, raw)
    return text.encode("utf-8") if text is not None else None


def _search_payload(candidate, remaining, deadline):
    relative = artifacts._location(candidate)[1]
    kind = media_kind(relative)
    if (is_image_relative_path(relative) or kind == "audio"
            or (candidate.suffix not in {".txt", ".log"} and kind != "documents")):
        return None
    if block := get_model_read_block_error(str(candidate)):
        raise PermissionError(block)
    raw = artifacts.read_bytes(candidate)
    if len(raw) > remaining or time.monotonic() >= deadline:
        raise ValueError("Private artifact search exceeded its byte/time bound; narrow the path")
    payload = _search_text(candidate, relative, raw)
    if payload is None:
        return None
    timeout = min(15, deadline - time.monotonic())
    used = max(len(raw), len(payload))
    if used > remaining or timeout <= 0:
        raise ValueError("Private document search exceeded its byte/time bound; narrow the path")
    return payload, timeout, used


def _search_content(candidates, pattern, limit, offset, output_mode, context):
    result = SearchResult()
    budget, hits, deadline = 0, [], time.monotonic() + 30
    for candidate in candidates:
        prepared = _search_payload(candidate, _SCAN_BYTES - budget, deadline)
        if prepared is None:
            continue
        raw, timeout, used = prepared
        budget += used
        if output_mode != "content":
            proc = _run_rg(raw, pattern, ["--count"], timeout)
            count = int(proc.stdout.strip() or b"0")
            if count:
                result.files.append(str(candidate))
                result.counts[str(candidate)] = count
            continue
        rows = _matching_lines(raw, pattern, max(0, min(context, 100)), offset + limit + 1, timeout)
        result.total_count += sum(row[2] for row in rows)
        hits.extend(SearchMatch(str(candidate), line, redact_sensitive_text(text[:500], file_read=True))
                    for line, text, _ in rows)
    if output_mode == "content":
        result.truncated = len(hits) > offset + limit
        result.matches = hits[offset:offset + limit]
    elif output_mode == "files_only":
        result.total_count = len(result.files)
        result.truncated = len(result.files) > offset + limit
        result.files, result.counts = result.files[offset:offset + limit], {}
    else:
        result.total_count, result.files = sum(result.counts.values()), []
    return result


def search(path, pattern, target, file_glob, limit, offset, output_mode, context, order):
    root = _admit(path)
    if root is None:
        return None
    if target not in {"content", "files"} or output_mode not in {"content", "files_only", "count"}:
        raise ValueError("Unsupported private artifact search mode")
    candidates = _candidates(root, file_glob, order)
    if target == "files":
        name = pattern.split("/")[-1]
        glob = name if name.startswith("*") else "*" + name
        selected = [str(p) for p in candidates if fnmatch.fnmatch(p.name, glob)]
        result = SearchResult(files=selected[offset:offset + limit], total_count=len(selected),
                              truncated=offset + limit < len(selected))
    else:
        result = _search_content(candidates, pattern, limit, offset, output_mode, context)
    rendered = result.to_dict(densify=True)
    rendered["source"] = _SOURCE
    return json.dumps(rendered, ensure_ascii=False)
