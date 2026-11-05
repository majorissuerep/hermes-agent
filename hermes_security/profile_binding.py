"""Logical profile identity over an original, possibly machine-owned vault resource.

Named machine-vault profiles authenticate their existing final-path envelope;
structural catalogue markers alone never authorize another encrypted store.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from hermes_security.errors import VaultLockedError, VaultNotInitializedError

_PROOF_BOUND = 8 * 1024 * 1024
_MAGIC = b"HRMVAULT\x00"


def _entry(path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def require_owned_metadata(home):
    from hermes_security.vault import _load_meta

    metadata = home / ".hermes-vault"
    if not metadata.is_file() or metadata.is_symlink() or _load_meta(home) is None:
        raise VaultLockedError("Private output requires this exact initialized profile")


def _directory(home):
    try:
        info = home.lstat()
    except OSError as error:
        raise VaultLockedError("Original profile directory is unavailable") from error
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise VaultLockedError("Encrypted profile directory is not an owned safe directory")
    return info.st_dev, info.st_ino


def _managed(home, owner):
    from hermes_constants import named_profile_home, named_profile_is_live

    if (home.parent.name != "profiles" or home.parent.parent != owner
            or named_profile_home(home) != home or not named_profile_is_live(home)):
        raise VaultLockedError("No authenticated managed profile at the selected home")


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_proof(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or info.st_mode & 0o077 or not 0 < info.st_size <= _PROOF_BOUND):
            raise VaultLockedError("Profile identity envelope must be an owned bounded private file")
        data = source.read(_PROOF_BOUND + 1)
    if not data.startswith(_MAGIC) or len(data) > _PROOF_BOUND:
        raise VaultLockedError("Profile identity requires a complete encrypted local envelope")
    if _stamp(path.lstat()) != _stamp(info):
        raise VaultLockedError("Profile identity envelope changed while reading")
    return data, _stamp(info)


def _proof_path(home):
    # Creation always seeds .env. Config-only older named profiles are supported;
    # a present damaged .env is never skipped in favour of another file.
    for name, purpose in ((".env", "env"), ("config.yaml", "config")):
        if _entry(home / name) is not None:
            return home / name, purpose
    raise VaultLockedError("Selected profile has no authenticated local credential/config store")


def owner_hint(home):
    """Read-only metadata/envelope-shape admission before explicit maintenance unlock."""
    home = Path(home).expanduser().resolve()
    if _entry(home) is None:
        raise VaultNotInitializedError("No initialized profile at the selected home")
    if _entry(home / ".hermes-vault") is not None:
        require_owned_metadata(home)
        return home
    owner = home.parent.parent
    _managed(home, owner)
    require_owned_metadata(owner)
    _read_proof(_proof_path(home)[0])
    return owner


class _VaultResource:
    def __init__(self, home, *, profile_home):
        from hermes_security.session_authority import SessionAuthorityError, capture_inherited_authority
        from hermes_security.vault import cached_custody_object, get_vault

        require_owned_metadata(home)
        self._identity = _directory(home)
        self.vault = cached_custody_object(home) or get_vault(home)
        if self.vault.home != home:
            raise VaultLockedError("Physical vault home changed while capturing custody")
        try:
            self.authority = (capture_inherited_authority(home, profile_home=profile_home)
                              if self.vault._borrowed else None)
        except SessionAuthorityError as error:
            raise VaultLockedError("No original inherited authority for this logical profile") from error
        self.revoked = False
        self.check()

    def check(self):
        from hermes_security.session_authority import SessionAuthorityError

        require_owned_metadata(self.vault.home)
        if _directory(self.vault.home) != self._identity:
            self.revoked = True
        if self.revoked:
            raise VaultLockedError("Private resource's original custody was revoked")
        try:
            self.vault.assert_custody_live()
            if self.authority is not None:
                self.authority.validate()
        except (VaultLockedError, SessionAuthorityError) as error:
            self.revoked = True
            raise VaultLockedError("Private resource's original custody was revoked") from error

    def derive_key(self, purpose):
        self.check()
        return self.vault.derive_key_for_custody(purpose, authority=self.authority)

    @property
    def home(self):
        return self.vault.home

    def encrypt(self, payload, *, purpose, relpath):
        self.check()
        return self.vault.encrypt(payload, purpose=purpose, relpath=relpath, authority=self.authority)

    def decrypt(self, payload, *, purpose, relpath):
        self.check()
        return self.vault.decrypt(payload, purpose=purpose, relpath=relpath, authority=self.authority)



class ProfileCustody:
    """One logical directory generation and its original physical unlock/lineage."""
    def __init__(self, home, resource, *, storage_home=None, _conversion=False):
        self.home = Path(home).resolve()
        self.storage_home = Path(storage_home).resolve() if storage_home is not None else self.home
        self._resource = resource
        self.vault, self.authority = resource.vault, resource.authority
        self._identity = _directory(self.storage_home)
        self._staged = storage_home is not None
        self._conversion = _conversion
        self._prefix = self.home.relative_to(self.vault.home)
        self._proof = None
        self.revoked = False
        self.check()

    def check(self):
        self._resource.check()
        if self.revoked or _directory(self.storage_home) != self._identity:
            self.revoked = True
            raise VaultLockedError("Original profile directory generation was replaced")
        if self._staged:
            if _entry(self.storage_home / ".hermes-vault") is not None or self.home.exists():
                raise VaultLockedError("Trusted profile publication target changed")
        elif self.home != self.vault.home:
            if _entry(self.home / ".hermes-vault") is not None:
                self.revoked = True
                raise VaultLockedError("The original machine-owned profile store changed")
            _managed(self.home, self.vault.home)
            if not self._conversion:
                self._check_proof()

    def _check_proof(self):
        path, purpose = _proof_path(self.home)
        try:
            stamp = _stamp(path.lstat())
        except OSError as error:
            raise VaultLockedError("Original profile identity store is unavailable") from error
        if self._proof is not None and stamp == self._proof[2]:
            return
        raw, stamp = _read_proof(path)
        self.vault.decrypt(raw, purpose=purpose, relpath=path.relative_to(self.vault.home).as_posix(),
                           authority=self.authority)
        self._resource.check()
        if _stamp(path.lstat()) != stamp:
            raise VaultLockedError("Profile identity envelope changed during authentication")
        self._proof = path, purpose, stamp

    def check_target(self, path):
        from hermes_constants import named_profile_home
        from hermes_security.vault import find_vault_home

        self.check()
        target = Path(path).expanduser().resolve(strict=False)
        if not target.is_relative_to(self.storage_home):
            raise PermissionError("Encrypted output belongs to a different logical profile")
        if find_vault_home(target) != self.vault.home:
            raise PermissionError("Encrypted output belongs to a different physical vault")
        named = named_profile_home(target)
        if not self._staged and named is not None and named != self.home:
            raise PermissionError("Encrypted output crosses a logical profile boundary")

    def aad_path(self, relative):
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionError("Encrypted state requires a profile-relative path")
        return (self._prefix / relative).as_posix()

    def derive_key(self, purpose):
        self.check()
        return self._resource.derive_key(_frame_label(purpose, self._prefix))

    def encrypt(self, payload, *, purpose, relpath):
        self.check()
        return self.vault.encrypt(payload, purpose=purpose, relpath=self.aad_path(relpath), authority=self.authority)

    def decrypt(self, payload, *, purpose, relpath):
        self.check()
        return self.vault.decrypt(payload, purpose=purpose, relpath=self.aad_path(relpath), authority=self.authority)


def capture(home):
    """Admit an exact own store or an authenticated managed machine-vault profile."""
    selected = Path(home).expanduser().resolve()
    return ProfileCustody(selected, _VaultResource(owner_hint(selected), profile_home=selected))


def bound(home, custody=None):
    selected = Path(home).expanduser().resolve()
    custody = capture(selected) if custody is None else custody
    if getattr(custody, "home", custody.vault.home) != selected:
        raise PermissionError("Private resource belongs to a different logical profile")
    custody.check()
    return custody


def logical_home(path):
    """Choose only a logical namespace; capture() separately proves its admission."""
    from hermes_constants import named_profile_home
    from hermes_security.vault import find_vault_home

    owner = find_vault_home(path)
    named = named_profile_home(Path(path).expanduser().resolve(strict=False))
    return named if named is not None and named.is_relative_to(owner) else owner


def for_file(path, custody=None):
    custody = capture(logical_home(path)) if custody is None else custody
    custody.check_target(path)
    return custody


def for_publication(storage_home, final_home):
    """Trusted producer only: bind a private staging inode to its explicit final profile."""
    storage, final = Path(storage_home).resolve(), Path(final_home).resolve()
    owner = final.parent.parent
    if final.parent.name != "profiles" or storage.parent != final.parent or not storage.name.startswith("."):
        raise PermissionError("Invalid explicit profile publication layout")
    from hermes_cli._profile_selection import PROFILE_ID_RE
    if not PROFILE_ID_RE.fullmatch(final.name):
        raise PermissionError("Invalid final logical profile name")
    from hermes_constants import get_hermes_home
    return ProfileCustody(final, _VaultResource(owner, profile_home=get_hermes_home()), storage_home=storage)


def for_conversion(path, owner):
    """Only the explicit migration plan may emit scoped frames before sealing its marker."""
    from hermes_security.vault import find_vault_home

    owner = Path(owner).resolve()
    if find_vault_home(path) != owner:
        raise VaultLockedError("Migration cannot borrow an independently owned profile's key")
    home = logical_home(path)
    return ProfileCustody(home, _VaultResource(owner, profile_home=owner), _conversion=home != owner)


def _frame_label(purpose, prefix):
    if prefix != Path(".") and purpose.startswith("frames:"):
        return "frames:profile|" + prefix.as_posix() + "|" + purpose[7:]
    return purpose


class _ArchiveFrameKey:
    def __init__(self, vault, relative):
        from hermes_cli._profile_selection import PROFILE_ID_RE

        self._resource = vault
        parts = Path(relative).parts
        self._prefix = (Path(*parts[:2]) if len(parts) > 2 and parts[0] == "profiles"
                        and PROFILE_ID_RE.fullmatch(parts[1]) else Path("."))

    def derive_key(self, purpose):
        return self._resource.derive_key(_frame_label(purpose, self._prefix))


def archive_frame_key(vault, relative):
    """Offline import codec only: exact destination-relative namespace, no runtime admission."""
    if not isinstance(vault, _VaultResource):
        from hermes_constants import get_hermes_home

        resource = _VaultResource(vault.home, profile_home=get_hermes_home())
        if resource.vault is not vault:
            raise VaultLockedError("Archive's original vault custody was replaced")
        vault = resource
    return _ArchiveFrameKey(vault, relative)
