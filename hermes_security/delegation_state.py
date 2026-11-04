"""Typed, path-bound delegation control state and user-readable private output."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading

from hermes_security import artifacts
from hermes_security.artifact_paths import is_delegation_log_path, is_delegation_manifest_path
from hermes_security.errors import VaultNotInitializedError

MANIFEST_PURPOSE = "delegation-manifest:v1"


def _require_profile_metadata(home):
    metadata = home / ".hermes-vault"
    if not metadata.is_file() or metadata.is_symlink():
        raise VaultNotInitializedError("Delegation state requires this exact initialized profile")


class DelegationStore:
    """An original profile unlock; callbacks never acquire a later ambient vault."""

    def __init__(self, home=None):
        if home is None:
            from hermes_constants import get_hermes_home

            home = get_hermes_home()
        self.configured_home = Path(os.path.abspath(Path(home).expanduser()))
        self.home = self.configured_home.resolve()
        _require_profile_metadata(self.home)
        self.custody = artifacts._bound_custody(self.home)
        self.manifest_lock = threading.RLock()

    def _manifest_location(self, path):
        selected, candidate = artifacts._binding(path, self.configured_home)
        try:
            relative = candidate.relative_to(selected)
        except ValueError as exc:
            raise PermissionError("Delegation manifest belongs to another profile") from exc
        if not is_delegation_manifest_path(relative):
            raise PermissionError("Not a delegation control manifest")
        _require_profile_metadata(selected)
        artifacts._bound_custody(selected, self.custody)
        return selected, relative

    def write_manifest(self, path, payload):
        selected, relative = self._manifest_location(path)
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(raw) > artifacts._MAX_BYTES:
            raise ValueError("Delegation manifest exceeds the supported bound")
        encoded = self.custody.vault.encrypt(raw, purpose=MANIFEST_PURPOSE,
                                             relpath=relative.as_posix(), authority=self.custody.authority)
        return artifacts._publish(selected, relative, encoded, self.custody, overwrite=True)

    def read_manifest(self, path):
        selected, relative = self._manifest_location(path)
        raw = artifacts._read_owned_bytes(selected, relative, artifacts._MAX_BYTES + 128)
        self.custody.check()
        decoded = self.custody.vault.decrypt(raw, purpose=MANIFEST_PURPOSE,
                                             relpath=relative.as_posix(), authority=self.custody.authority)
        payload = json.loads(decoded)
        if not isinstance(payload, dict):
            raise ValueError("Delegation manifest must be an object")
        return payload

    def create_log(self, path, text):
        self._log_location(path)
        return artifacts.write_bytes(path, text.encode("utf-8"), home=self.configured_home, custody=self.custody)

    def _log_location(self, path):
        selected, relative = artifacts._location(path, self.configured_home)
        _require_profile_metadata(selected)
        if not is_delegation_log_path(relative):
            raise PermissionError("Not a delegation transcript")

    def append_log(self, path, text):
        self._log_location(path)
        if len(text) > artifacts._MAX_BYTES:
            raise ValueError("Delegation append exceeds the supported byte bound")
        payload = text.encode("utf-8")
        previous = artifacts.read_bytes(path, home=self.configured_home, custody=self.custody)
        if len(previous) + len(payload) > artifacts._MAX_BYTES:
            raise ValueError("Delegation transcript exceeds the supported byte bound")
        return artifacts.write_bytes(path, previous + payload, home=self.configured_home,
                                     custody=self.custody, overwrite=True)

    def read_log(self, path):
        self._log_location(path)
        return artifacts.read_bytes(path, home=self.configured_home, custody=self.custody)


def read_transcript(path, *, home=None, store=None):
    store = DelegationStore(home) if store is None else store
    return store.read_log(path)


def read_manifest(path, *, home=None):
    return DelegationStore(home).read_manifest(path)
