"""Original-request admission, separate from a profile's shared MCP connection."""
from agent.session_context import get_session_env
from hermes_constants import get_hermes_home
from hermes_security.errors import VaultError
from tools.media_cache import output_custody, retention_owner
from tools.registry import tool_error


class RequestRefused(str):
    """Locally generated refusal; never evidence that the MCP server is unhealthy."""


def original_request_check():
    """Capture once before approval/discovery; a later unlock cannot renew this request.

    Standalone sessionless text/control clients retain their existing behavior when
    encrypted retention was unavailable from entry. A selected native session must
    have its own authority; private output still requires custody in every case.
    """
    owner = retention_owner()
    selected = bool(get_session_env('HERMES_SESSION_ID', ''))

    def refusal(*, uncertain=False):
        if owner[1] is None and not selected:
            return None
        try:
            if get_hermes_home().resolve() != owner[0]:
                raise ValueError('The selected profile changed')
            output_custody(owner)
        except (VaultError, OSError, ValueError):
            if uncertain:
                return RequestRefused(tool_error(
                    'The requesting session has ended and this command may have completed. '
                    'Verify its external effect before invoking it again.', outcome_uncertain=True))
            return RequestRefused(tool_error(
                'This request no longer has access to its original session. '
                'Run it again from an active session.'))
        return None

    return refusal
