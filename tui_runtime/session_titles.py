"""Owned title generation and publication, separate from conversation mutation."""
import contextlib
import logging

logger = logging.getLogger(__name__)


def _require_owner(session, sid):
    from tui_runtime import server

    if session.get("_finalized") or server._sessions.get(sid) is not session:
        raise ValueError("The session was closed while naming it.")


def _title_identity(row):
    return (row.get("title"), row.get("title_source")) if row else None


def _commit_generated(db, key, title, expected):
    def write(conn):
        row = conn.execute("SELECT title, title_source FROM sessions WHERE id = ?", (key,)).fetchone()
        if _title_identity(dict(row) if row else None) != expected:
            raise ValueError("This session was renamed while the model was working; keeping that name.")
        return db._set_session_title_in_transaction(conn, key, title, source=db.TITLE_SOURCE_USER)

    if not db._execute_write(write):
        raise ValueError("The session could not be renamed.")


def _write_manual(session, db, key, title):
    from tui_runtime import server

    if db.set_session_title(key, title) or db.get_session(key):
        return False, db.get_session_title(key) or ""
    server._ensure_session_db_row(session)
    with server._session_db(session) as scoped_db:
        if scoped_db is not None and scoped_db.set_session_title(key, title):
            return False, scoped_db.get_session_title(key) or ""
    return True, title


def session_title(rid, params, session, db):
    from hermes_cli.session_naming import generate_session_name
    from hermes_state import SessionDB
    from tui_runtime import server

    sid, key = params.get("session_id", ""), session["session_key"]
    lock = session.get("history_lock") or contextlib.nullcontext()
    try:
        generated = bool(params.get("generate"))
        if generated:
            with lock:
                _require_owner(session, sid)
                expected = _title_identity(db.get_session(key))
                revision = session.get("_title_edit_revision", 0)
                messages = db.get_messages_as_conversation(key)
                runtime = getattr(session.get("agent"), "_primary_runtime", None)
            # No session/history lock spans a model request: manual renames and shutdown stay live.
            title = generate_session_name(messages, main_runtime=runtime)
            if not title:
                raise ValueError("The naming model returned no usable title.")
        with lock:
            _require_owner(session, sid)
            if generated:
                if session.get("_title_edit_revision", 0) != revision:
                    raise ValueError("This session was renamed while the model was working; keeping that name.")
                _commit_generated(db, key, title, expected)
                session["pending_title"] = None
                session["_title_edit_revision"] = revision + 1
                result = {"pending": False, "title": db.get_session_title(key) or ""}
            elif "title" not in params:
                result = {"title": server._title_read(session, db, key), "session_key": key}
            elif not (title := SessionDB.sanitize_title(params.get("title"))):
                return server._err(rid, 4021, "title required")
            else:
                pending, value = _write_manual(session, db, key, title)
                session["pending_title"] = value if pending else None
                session["_title_edit_revision"] = session.get("_title_edit_revision", 0) + 1
                result = {"pending": pending, "title": value}
    except ValueError as exc:
        return server._err(rid, 4022, str(exc))
    except Exception as exc:
        logger.exception("Session title RPC failed for session %s", key)
        return server._err(rid, 5007, str(exc))
    server._emit_session_info_for_session(sid, session)
    return server._ok(rid, result)
