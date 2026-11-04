"""Bind a SessionDB generation and every future open to its original custody."""
from __future__ import annotations


def generation_custody_current(db):
    """Local eligibility only: registry callers must not perform upstream IPC under their lock."""
    custody = getattr(db, "_encrypted_custody", None)
    return custody is None or custody.is_current()


class SessionCustodyMixin:
    def _assert_database_custody(self):
        if self._encrypted_custody is not None:
            self._encrypted_custody.assert_live()

    def _bind_database_custody(self, path):
        from hermes_state import _ensure_test_isolation
        from hermes_security.sqlite_custody import capture_database_custody

        _ensure_test_isolation(path)
        self.db_path = path
        self._encrypted_custody = capture_database_custody(path)

    def _connect_with_custody(self, path, **kwargs):
        from hermes_state import _connect_tracked_db

        self._assert_database_custody()
        if self._encrypted_custody is not None:
            kwargs["custody"] = self._encrypted_custody
        return _connect_tracked_db(path, **kwargs)

    def _connect_read_only(self, timeout):
        """Every read-pool open keeps this instance's original key custody and Row factory."""
        from hermes_state import _vaulted_row_factory, read_only_db_uri

        connection = self._connect_with_custody(
            read_only_db_uri(self.db_path), tracking_path=self.db_path, uri=True,
            check_same_thread=False, timeout=timeout, isolation_level=None,
        )
        connection.row_factory = _vaulted_row_factory(self.db_path)
        return connection
