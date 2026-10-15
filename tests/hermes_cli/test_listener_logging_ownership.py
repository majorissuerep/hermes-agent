"""Creating another listener must not shut down the process's logging handlers."""
import logging
import threading

import pytest


class _OwnedHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.closed_by_listener = threading.Event()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())

    def close(self):
        self.closed_by_listener.set()
        super().close()


@pytest.mark.parametrize("surface", ["session", "dashboard"])
def test_listener_preserves_the_process_logging_owner(surface, monkeypatch):
    from hermes_cli import web_server
    from hermes_cli.web_server_lifecycle import build_listener_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    logger = logging.getLogger(f"hermes.listener-logging-owner.{surface}")
    previous = logger.level, logger.propagate, logger.disabled
    owned = _OwnedHandler()
    logger.addHandler(owned)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.disabled = False
    try:
        if surface == "session":
            build_listener_server(web_server.app, "127.0.0.1", 0, auth_required=False)
        else:
            web_server._build_uvicorn_server("127.0.0.1", 0)
        assert not owned.closed_by_listener.is_set(), "listener construction shut down the owner's handlers"
        logger.info("the existing logging pipeline still owns delivery")
        assert owned.messages == ["the existing logging pipeline still owns delivery"]
    finally:
        logger.removeHandler(owned)
        logger.setLevel(previous[0])
        logger.propagate, logger.disabled = previous[1:]
        owned.close()
