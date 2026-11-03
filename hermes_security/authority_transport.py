"""Bounded operation frames over the existing private session transport."""
from __future__ import annotations

import json

LIMIT = 1048576


def request_operation(grant: dict, operation: dict) -> dict:
    """The caller cannot replace identity fields with operation payload fields."""
    if set(operation) & {"token", "home", "session_id", "request_id"}:
        raise ValueError("operation cannot select grant identity")
    if "pipe" in grant:
        from hermes_security.pipe_authority import redeem
        return redeem(grant, operation)
    from hermes_security.session_authority import _connection
    request = {**operation, **{key: grant[key] for key in ("token", "home", "session_id")}}
    payload = json.dumps(request, allow_nan=False).encode("utf-8") + b"\n"
    if len(payload) > LIMIT:
        raise ValueError("authority request is too large")
    with _connection(grant) as connection:
        connection.sendall(payload)
        with connection.makefile("rb") as reader:
            response = reader.readline(LIMIT + 1)
    if len(response) > LIMIT or not response.endswith(b"\n"):
        raise ValueError("authority response is too large or incomplete")
    return json.loads(response)
