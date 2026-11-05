"""One private operation's original session, separate from shared profile resources.

A resource retains its owning home and Vault generation. Each queued operation additionally
captures its exact local source, or its original trusted native-child lineage. Neither a live
sibling nor a later human unlock can renew an old operation. Grants stay in RAM only.
"""
from __future__ import annotations

from pathlib import Path

from hermes_security.errors import VaultError, VaultLockedError


class SessionCustody:
    """A custody-compatible operation guard; ``resource`` remains independently shareable."""

    def __init__(self, home: Path, resource, source: str | None) -> None:
        self.home, self.resource = home, resource
        self._source, self._revoked = source, False

    @classmethod
    def capture(cls, home: Path | str, *, resource=None) -> SessionCustody:
        from agent.session_context import get_session_env
        from hermes_security.artifacts import _bound_custody
        from hermes_security.session_authority import ENV_NAME, session_environment

        home = Path(home).expanduser().resolve()
        resource = _bound_custody(home, resource)
        session_id = get_session_env("HERMES_SESSION_ID", "")
        source = session_environment(home, session_id).get(ENV_NAME) if session_id else None
        if session_id and source is None:
            inherited = resource.authority
            if inherited is None or not inherited.names_session(home, session_id):
                raise VaultLockedError("The operation's selected session is no longer live")
        operation = cls(home, resource, source)
        operation.check()
        return operation

    @property
    def vault(self):
        return self.resource.vault

    @property
    def authority(self):
        return self.resource.authority

    def check(self) -> None:
        from hermes_security.session_authority import SessionAuthorityError, validate_local_source_grant

        if self._revoked:
            raise VaultLockedError("The private operation's original session was revoked")
        try:
            self.resource.check()
            if self._source is not None:
                validate_local_source_grant(self.home, self._source)
        except (VaultError, SessionAuthorityError) as exc:
            self._revoked = True
            raise VaultLockedError("The private operation's original session was revoked") from exc

    def derive_key(self, purpose: str) -> bytes:
        self.check()
        return self.resource.derive_key(purpose)

    def check_target(self, path) -> None:
        self.check()
        self.resource.check_target(path)

    def aad_path(self, relative) -> str:
        self.check()
        return self.resource.aad_path(relative)

    def encrypt(self, payload, *, purpose, relpath):
        self.check()
        return self.resource.encrypt(payload, purpose=purpose, relpath=relpath)

    def decrypt(self, payload, *, purpose, relpath):
        self.check()
        return self.resource.decrypt(payload, purpose=purpose, relpath=relpath)

    def __repr__(self) -> str:
        return "<SessionCustody>"

    def __reduce__(self):
        raise TypeError("Private operation custody cannot be serialized")
