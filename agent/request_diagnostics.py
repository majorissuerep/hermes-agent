"""Encrypted provider-request diagnostics, bound to an agent's original owning profile."""
from __future__ import annotations

import copy
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any

from utils import env_var_enabled

logger = logging.getLogger(__name__)


class _RequestStore:
    def __init__(self, agent, home):
        from hermes_security.profile_binding import capture

        self.home = Path(home).resolve()
        if (self.home / "sessions").is_symlink():
            raise PermissionError("Request diagnostics cannot use a symlinked sessions directory")
        self.custody = capture(self.home)
        self.vault, self.authority = self.custody.vault, self.custody.authority
        self.session = getattr(agent, "_vault_session_authority", None)

    def write(self, path, payload):
        from hermes_security import vault
        from hermes_security.errors import VaultLockedError

        if path.parent != self.home / "sessions" or path.parent.is_symlink():
            raise PermissionError("Request diagnostics must remain in the original profile's sessions directory")
        self.custody.check_target(path)
        if self.session is not None and not self.session.is_live():
            raise VaultLockedError("The request diagnostic's owning session ended")
        self.custody.check()
        serialized = (json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")
        encoded = self.custody.encrypt(serialized, purpose="transcript",
                                       relpath=path.relative_to(self.home).as_posix())
        vault._atomic_write(path, encoded)  # Only ciphertext reaches the existing atomic state writer.


def bind_request_diagnostics(agent, *, home=None):
    """Capture original home/key lineage at construction; unavailable custody disables persistence."""
    from hermes_constants import get_hermes_home
    from hermes_security.errors import VaultError

    selected = Path(home or get_hermes_home()).expanduser().resolve()
    agent.logs_dir = selected / "sessions"
    try:
        agent.logs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        agent._request_diagnostic_store = _RequestStore(agent, selected)
    except (OSError, VaultError):
        agent._request_diagnostic_store = None


def _api_error_debug_info(error):
    info = {"type": type(error).__name__, "message": str(error)}
    info.update({key: value for key in ("status_code", "request_id", "code", "param", "type", "body")
                 if (value := getattr(error, key, None)) is not None})
    response = getattr(error, "response", None)
    if response is not None:
        try:
            info.update(response_status=getattr(response, "status_code", None), response_text=response.text)
        except Exception:
            logger.debug("Could not extract error response details", exc_info=True)
    return info


def _client_api_key(agent):
    try:
        client = getattr(agent, "_anthropic_client", None) if agent.api_mode == "anthropic_messages" else agent.client
        return getattr(client, "api_key", None) or getattr(client, "auth_token", None)
    except Exception:
        logger.debug("Could not extract API key for debug dump", exc_info=True)
        return None


def dump_api_request_debug(agent, api_kwargs: dict[str, Any], *, reason: str, error=None):
    """Keep diagnostic JSON/masked headers intact while encrypting every persisted byte."""
    from hermes_security.errors import VaultLockedError

    try:
        if not hasattr(agent, "_request_diagnostic_store"):
            # Existing embedded callers still bind from their already-captured output directory.
            bind_request_diagnostics(agent, home=Path(agent.logs_dir).parent)
        store = agent._request_diagnostic_store
        if store is None:
            raise VaultLockedError("Encrypted request diagnostic storage is unavailable")
        body = {key: value for key, value in copy.deepcopy(api_kwargs).items() if value is not None and key != "timeout"}
        key = _client_api_key(agent)
        endpoint = {"codex_responses": "/responses", "anthropic_messages": "/messages"}.get(
            agent.api_mode, "/chat/completions")
        payload = {"timestamp": datetime.now().isoformat(), "session_id": agent.session_id, "reason": reason,
                   "request": {"method": "POST", "url": f"{agent.base_url.rstrip('/')}{endpoint}",
                               "headers": {"Authorization": f"Bearer {agent._mask_api_key_for_logs(key)}",
                                           "Content-Type": "application/json"}, "body": body}}
        if error is not None:
            payload["error"] = _api_error_debug_info(error)
        from agent.redact import redact_sensitive_text
        from agent.session_persistence import _safe_session_filename_component

        safe_sid = _safe_session_filename_component(agent.session_id)
        target = store.home / "sessions" / f"request_dump_{safe_sid}_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
        redacted = json.loads(redact_sensitive_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), force=True))
        store.write(target, redacted)
        agent._vprint(f"{agent.log_prefix}🧾 Encrypted request debug dump written to: {target}. "
                      "Use the existing offline secure-vault export for plaintext forensics.")
        if env_var_enabled("HERMES_DUMP_REQUEST_STDOUT"):
            print(json.dumps(redacted, ensure_ascii=False, indent=2, default=str))
        return target
    except Exception:
        logger.debug("Encrypted API request debug payload was not retained", exc_info=True)
        agent._vprint(f"{agent.log_prefix}Request debug dump was not retained: encrypted profile storage unavailable.")
        return None
