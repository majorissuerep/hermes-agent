"""SQLCipher handles retain their original custody and native DB-API factories.

Admission checks precede cached statements and each retained-cursor fetch. They
do not claim to sandbox native callers invoking the driver's unbound C methods.
Cleanup remains available after revocation; a fresh unlock needs fresh handles.
"""
from __future__ import annotations

from functools import wraps

from hermes_security.errors import VaultLockedError


class SQLCipherCustody:
    def __init__(self, vault):
        from hermes_security.session_authority import capture_inherited_authority

        vault._require_live()
        self._vault = vault
        self._authority = capture_inherited_authority(vault.home) if vault._borrowed else None
        self._revoked = False
        self.assert_live()

    def assert_live(self):
        from hermes_security.session_authority import SessionAuthorityError

        if self._revoked:
            raise VaultLockedError("The encrypted database's original custody was revoked")
        try:
            self._vault.assert_custody_live()
            if self._authority is not None:
                self._authority.validate()
        except (VaultLockedError, SessionAuthorityError) as exc:
            self._revoked = True
            raise VaultLockedError("The encrypted database's original custody was revoked") from exc

    def is_current(self):
        return (not self._revoked and self._vault.custody_is_current()
                and (self._authority is None or self._authority.is_current()))

    def derive_key(self, purpose):
        self.assert_live()
        return self._vault.derive_key_for_custody(purpose, authority=self._authority)

    def __repr__(self):
        return "<SQLCipherCustody>"

    def __reduce__(self):
        raise TypeError("Encrypted database custody cannot be pickled")


def capture_database_custody(path):
    from hermes_security import sqlite
    from hermes_security.vault import find_vault_home, get_vault

    return SQLCipherCustody(get_vault(find_vault_home(path))) if sqlite.is_vaulted(path) else None


def _guarded(method):
    @wraps(method)
    def checked(self, *args, **kwargs):
        self._assert_custody()
        return method(self, *args, **kwargs)
    return checked


class _CursorGuard:
    __slots__ = ()

    def _assert_custody(self):
        self.connection._assert_custody()


class _CustodyBlob:
    """The native incremental-blob API keeps the same connection admission rule."""
    def __init__(self, blob, connection):
        self._blob, self._connection = blob, connection

    def __getattr__(self, name):
        self._connection._assert_custody()
        value = getattr(self._blob, name)
        if not callable(value):
            return value

        def checked(*args, **kwargs):
            self._connection._assert_custody()
            return value(*args, **kwargs)
        return checked

    def __len__(self):
        self._connection._assert_custody()
        return len(self._blob)

    def __getitem__(self, key):
        self._connection._assert_custody()
        return self._blob[key]

    def __setitem__(self, key, value):
        self._connection._assert_custody()
        self._blob[key] = value

    def __enter__(self):
        self._connection._assert_custody()
        return self

    def close(self):
        return self._blob.close()

    def __exit__(self, *args):
        self.close()
        return False


class _ConnectionGuard:
    _hermes_custody = None

    def _assert_custody(self):
        if self._hermes_custody is not None:
            self._hermes_custody.assert_live()

    def cursor(self, factory=None):
        self._assert_custody()
        chosen = guarded_cursor_factory(factory or self._hermes_cursor_base)
        result = super().cursor(factory=chosen)
        if not isinstance(result, _CursorGuard):
            result.close()
            raise TypeError("SQLCipher cursor factory discarded custody protection")
        return result

    def __enter__(self):
        self._assert_custody()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            try:
                self._assert_custody()
            except VaultLockedError:
                self.rollback()
                raise
        return super().__exit__(exc_type, exc_value, traceback)

    def backup(self, target, *args, **kwargs):
        self._assert_custody()
        if isinstance(target, _ConnectionGuard):
            target._assert_custody()
        return super().backup(target, *args, **kwargs)

    def open_blob(self, *args, **kwargs):
        self._assert_custody()
        return _CustodyBlob(super().open_blob(*args, **kwargs), self)

    @property
    def isolation_level(self):
        return super().isolation_level

    @isolation_level.setter
    def isolation_level(self, value):
        self._assert_custody()
        super(_ConnectionGuard, type(self)).isolation_level.__set__(self, value)


_CONNECTION_FACTORIES = {}
_CURSOR_FACTORIES = {}
_CONNECTION_OPERATIONS = (
    "execute", "executemany", "executescript", "commit", "create_function", "create_aggregate",
    "create_window_function", "create_collation", "enable_load_extension", "load_extension", "set_authorizer",
    "set_busy_handler", "set_busy_timeout", "set_progress_handler", "set_trace_callback", "set_key", "reset_key",
)
_CURSOR_OPERATIONS = ("execute", "executemany", "executescript", "fetchone", "fetchmany", "fetchall",
                      "__iter__", "__next__")


def _methods(base, operations):
    return {name: _guarded(getattr(base, name)) for name in operations if hasattr(base, name)}


def _read_fields(base, names):
    def field(descriptor):
        @_guarded
        def read(self):
            return descriptor.__get__(self, type(self))
        return property(read)
    return {name: field(getattr(base, name)) for name in names if hasattr(base, name)}


def guarded_cursor_factory(base):
    if not isinstance(base, type):
        raise TypeError("Encrypted cursor factories must be native Cursor subclasses")
    if issubclass(base, _CursorGuard):
        return base
    if base not in _CURSOR_FACTORIES:
        methods = {**_methods(base, _CURSOR_OPERATIONS),
                   **_read_fields(base, ("description", "rowcount", "lastrowid")), "__slots__": ()}
        _CURSOR_FACTORIES[base] = type("Custody" + base.__name__, (_CursorGuard, base), methods)
    return _CURSOR_FACTORIES[base]


def guarded_connection_factory(base, driver):
    if not isinstance(base, type) or not issubclass(base, driver.Connection):
        raise TypeError("Encrypted connection factories must be native SQLCipher Connection subclasses")
    if issubclass(base, _ConnectionGuard):
        return base
    if base not in _CONNECTION_FACTORIES:
        methods = {**_methods(base, _CONNECTION_OPERATIONS), **_read_fields(base, ("total_changes",)),
                   "_hermes_cursor_base": driver.Cursor}
        _CONNECTION_FACTORIES[base] = type("Custody" + base.__name__, (_ConnectionGuard, base), methods)
    return _CONNECTION_FACTORIES[base]
