"""Passive collection of retired historical media with ownership and transcript fences."""
import hashlib
import json
from pathlib import Path
import re
import stat
import threading

_COLLECTION_ROOT_SUBPATH = 'cache/documents'
_collection_lock = threading.Lock()
_API_IMAGE_NAME = re.compile(r'api_[0-9a-f]{32}\.(png|jpg|gif|webp)')
_SHA256_NAME = re.compile(r'[0-9a-f]{64}')
HOSTED_DOCUMENT_LINE = '\n[Shared attachment] file: '
_HOSTED_DOCUMENT_RE = re.compile(re.escape(HOSTED_DOCUMENT_LINE) + r'([^\n]+)\n')


def _media_root():
    from hermes_constants import get_hermes_dir
    return get_hermes_dir(_COLLECTION_ROOT_SUBPATH, 'document_cache').resolve() / 'native-inputs'


def hosted_document_references(request_id, payload):
    """Retained document references a hosted admission's prompt names (``[]`` for other rows).

    Only content-addressed names under this profile's retained root count, and never an API
    image name: those are held by ``api_turn_v1`` in every status, so a member's prompt text
    can name one without gaining deletion authority over it."""
    if not str(request_id).startswith('hosted:') or not isinstance(payload.get('text'), str):
        return []
    root = _media_root()
    references = []
    for value in _HOSTED_DOCUMENT_RE.findall(payload['text']):
        path = Path(value)
        if (path.is_absolute() and path.parent.parent == root and _SHA256_NAME.fullmatch(path.parent.name)
                and not _API_IMAGE_NAME.fullmatch(path.name)):
            references.append({'path': value, 'sha256': path.parent.name})
    return references


def admission_media_references(payload):
    """Native references eligible as deletion candidates after terminal settlement."""
    return list(payload.get('attachments_v1', {}).get('media', ())) + list(
        payload.get('native_text_v1', {}).get('media', ()))


def _held_media_paths(conn):
    # Project only references, not potentially large inline-image/history payloads; a live hosted
    # row's prompt is projected too, because its documents are held by text, not by a reference.
    rows = conn.execute("""SELECT status, request_id, json_extract(payload_json,
            '$.attachments_v1.media', '$.native_text_v1.media', '$.api_turn_v1.media'),
            CASE WHEN status!='terminal' AND request_id LIKE 'hosted:%'
                 THEN json_extract(payload_json, '$.text') END
            FROM session_admissions WHERE status!='terminal'
            OR json_type(payload_json, '$.api_turn_v1.media') IS NOT NULL""").fetchall()
    held = set()
    for status, request_id, encoded, text in rows:
        attachments, native, api = json.loads(encoded)
        # API images remain canonical history context after the turn completes.
        references = list(api or ())
        if status != 'terminal':
            references.extend(attachments or ())
            references.extend(native or ())
            references.extend(hosted_document_references(request_id, {'text': text}))
        held.update(reference['path'] for reference in references)
    return held


def _file_identity(path):
    saved = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(saved.st_mode) or not saved.st_ino:
        raise ValueError('uncertain native file identity')
    return saved.st_dev, saved.st_ino


def _held_file_identities(paths, root):
    identities = set()
    try:
        for value in paths:
            path = Path(value)
            if path.parent.parent != root or path.parent.resolve() != path.parent:
                return None
            identities.add(_file_identity(path))
    except (OSError, ValueError):
        return None
    return identities


def _history_mentions(conn, references, after_id=0):
    """Candidate paths any transcript row (id > after_id) mentions raw or JSON-escaped, in ONE
    messages pass. Rows are prefiltered in SQL on the literal text before each candidate's digest
    directory, so only rows that could name a candidate reach Python's exact substring test."""
    # Matched by the content-addressed tail ``<sha256>/<name>`` with either separator, so every
    # spelling a note can carry (the host path, a sandbox backend's ``to_agent_visible_cache_path``)
    # counts as a mention.
    needles = {}
    for reference in references:
        raw = reference['path']
        for tail in (reference['sha256'] + sep + Path(raw).name for sep in ('/', '\\')):
            needles[tail] = needles[json.dumps(tail)[1:-1]] = raw
    prefixes = sorted({reference['sha256'] for reference in references})
    match = ' OR '.join(['instr(content,?)'] * len(prefixes))
    query = f'SELECT content FROM messages WHERE ({match})' + (' AND id>?' if after_id else '')
    mentioned = set()
    for (content,) in conn.execute(query, (*prefixes, *((after_id,) if after_id else ()))):
        text = content.decode('utf-8', 'replace') if isinstance(content, bytes) else str(content)
        mentioned.update(raw for needle, raw in needles.items() if needle in text)
    return mentioned


def _collectable(references, root):
    return [reference for reference in references if Path(reference['path']).parent.parent == root
            and Path(reference['path']).parent.name == reference['sha256'] and Path(reference['path']).exists()]


def release_unheld_media(db, references, *, retain_history=True):
    """Delete unowned references, preserving accepted admission and retained transcript owners.

    The history owner check is one read-snapshot pass over messages, outside the write lock; under
    the lock only rows inserted after that snapshot (an indexed id range) are re-checked. A new
    transcript mention of a retained image comes from an admission holding it (re-checked under the
    lock) or from a copied row (branch/compress insert, caught by the range)."""
    root = _media_root()
    references = _collectable(references or (), root)
    if not references:
        return 0
    seen, last_id = set(), 0
    if retain_history:
        with db._read_ctx() as conn:
            conn.execute('BEGIN')  # one snapshot: the id watermark and the scan agree
            try:
                last_id = conn.execute('SELECT coalesce(max(id), 0) FROM messages').fetchone()[0]
                seen = _history_mentions(conn, references)
            finally:
                if conn.in_transaction:
                    conn.execute('ROLLBACK')
        references = [reference for reference in references if reference['path'] not in seen]
        if not references:
            return 0
    def collect(conn):
        held = _held_media_paths(conn)
        identities = _held_file_identities(held, root)
        if identities is None:
            return 0
        if retain_history:
            held |= _history_mentions(conn, references, after_id=last_id)
        released = 0
        for reference in references:
            path = Path(reference['path'])
            if reference['path'] in held:
                continue
            try:
                with _collection_lock:
                    if (path.parent.resolve() != path.parent
                            or _file_identity(path) in identities):
                        continue
                    path.unlink()
                released += 1
                path.parent.rmdir()
            except (OSError, ValueError):
                continue
        return released
    return db._execute_write(collect)


PREFIX = 'gateway.media.retired.v1.'


def retire_media(conn, payload, request_id=''):
    references = [*admission_media_references(payload), *hosted_document_references(request_id, payload),
                  *payload.get('api_turn_v1', {}).get('media', [])]
    for reference in references:
        key = PREFIX + hashlib.sha256(reference['path'].encode()).hexdigest()
        conn.execute('INSERT OR IGNORE INTO state_meta(key,value) VALUES(?,?)',
                     (key, json.dumps(reference)))


def collect_retired_media(db):
    """Release retired candidates no admission or retained transcript row owns; drop collected keys.
    Synchronous SQLite plus one messages pass: async callers must run it off the event loop."""
    with db._read_ctx() as conn:
        # Primary-key range, not LIKE: the common no-candidate case costs one index probe.
        rows = conn.execute('SELECT key,value FROM state_meta WHERE key>=? AND key<?',
                            (PREFIX, PREFIX[:-1] + chr(ord(PREFIX[-1]) + 1))).fetchall()
    if rows:
        release_unheld_media(db, [json.loads(row['value']) for row in rows])
        from pathlib import Path
        gone = [(row['key'],) for row in rows if not Path(json.loads(row['value'])['path']).exists()]
        if gone:
            db._execute_write(lambda conn: conn.executemany('DELETE FROM state_meta WHERE key=?', gone))
