"""Operation-only management in the exact owning profile; no executable dispatch."""
from __future__ import annotations

def dispatch_management(authority, request: dict) -> dict:
    from hermes_cli.management import execute, _request
    from hermes_cli.management_config import ManagementError
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    from agent.session_context import scoped_current_session_id

    home_token = set_hermes_home_override(authority.home)
    secret_token = set_secret_scope({}, profile_home=str(authority.home))
    try:
        operation = _request(request.get("request"))
        if operation["op"] in {"browser.outgoing.repair", "browser.worker.repair"}:
            raise ManagementError("invalid_request", "use the public 'hermes manage apply' native command for bounded outgoing repair")
        secret = None
        if operation["op"] == "passphrase.change":
            raise ManagementError("consent_required", "changing the passphrase requires the owning human's consent")
        if "secret_fd" in operation:
            raise ManagementError("invalid_request", "remote file descriptors are not management inputs")
        if operation["op"] == "secret.set":
            secret = request.get("secret")
            if (not isinstance(secret, str) or not secret or len(secret.encode("utf-8")) > 65536
                    or any(char in secret for char in ("\0", "\n", "\r"))):
                raise ManagementError("invalid_request", "secret input must be one bounded UTF-8 value without newlines")
        elif "secret" in request:
            raise ManagementError("invalid_request", "this operation does not take a secret")
        with scoped_current_session_id(authority.session_id):
            result = execute(operation, authority.home, secret_value=secret)
        if operation["op"] == "status":
            from hermes_cli.management_status import requester_status
            result["requester"] = requester_status("operations_only")
        return {"ok": True, "result": result}
    except ManagementError as exc:
        return {"ok": False, "management_error": {"code": exc.code, "message": str(exc)}}
    except (OSError, ValueError, TypeError, RuntimeError):
        return {"ok": False, "management_error": {"code": "state_refused", "message": "owning management operation refused"}}
    finally:
        reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)


def proxy_management(request: dict, home, raw: str):
    from hermes_cli.management import _secret
    from hermes_cli.management_config import ManagementError
    from hermes_security.session_authority import operation_request

    operation = dict(request)
    payload = {"op": "manage", "request": operation}
    if operation["op"] == "passphrase.change":
        raise ManagementError("consent_required", "changing the passphrase requires the owning human's consent")
    if "secret_fd" in operation:
        payload["secret"] = _secret(operation)
        del operation["secret_fd"]
    response = operation_request(home.resolve(), raw, payload)
    if response.get("ok") is not True:
        error = response.get("management_error", {})
        raise ManagementError(error.get("code", "state_refused"), error.get("message", "owning management operation refused"))
    return response["result"]
