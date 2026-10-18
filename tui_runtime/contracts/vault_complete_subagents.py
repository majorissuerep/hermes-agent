"""Local credential metadata, composer completion and session-owned subagent contracts."""

from __future__ import annotations

from pydantic import Field

from .base import JsonValue, Params, Result, WireEnum
from .common import ProfileParams, SessionParams, SubagentStatus
from .config_free_tier_control import ModelOptionProvider
from .registry import method

# ── completions / paste / model keys (methods_complete) ───────────────────────────────────────


class CompletionItem(Result):
    """One popover row; ``kind`` rides only on slash completions (command vs skill)."""

    text: str
    display: str = ""
    meta: str = ""
    kind: str | None = None


class CompletionItemsResult(Result):
    items: list[CompletionItem] = Field(default_factory=list)


class CompletePathParams(ProfileParams):
    """``word`` is the token under the cursor (``@`` prefix = context reference); ``cwd`` /
    ``session_id`` pick the directory the listing resolves against."""

    word: str | None = None
    cwd: str | None = None
    session_id: str | None = None


method("complete.path", params=CompletePathParams, result=CompletionItemsResult,
       doc="Path / @-reference completions for the composer (files, folders, profiles, plugin providers).")


class CompleteSlashParams(Params):
    """``session_id`` binds skill completions to that session's profile and workspace (project skills);
    ``profile`` scopes a session-less request (a new-chat draft)."""

    text: str | None = None
    session_id: str | None = None
    profile: str | None = None


class CompleteSlashResult(Result):
    """``replace_from`` is the column the accepted item replaces from."""

    items: list[CompletionItem] = Field(default_factory=list)
    replace_from: int | None = None


method("complete.slash", params=CompleteSlashParams, result=CompleteSlashResult,
       doc="Ranked slash-command / skill completions for a ``/`` token.")


class PasteCollapseParams(Params):
    text: str | None = None


class PasteCollapseResult(Result):
    placeholder: str
    path: str
    lines: int


method("paste.collapse", params=PasteCollapseParams, result=PasteCollapseResult,
       doc="Spill a large paste to a file and hand back the inline placeholder.")


class ModelSaveKeyParams(Params):
    slug: str
    api_key: str
    session_id: str | None = None


class ModelSaveKeyResult(Result):
    provider: ModelOptionProvider


method("model.save_key", params=ModelSaveKeyParams, result=ModelSaveKeyResult,
       doc="Save an API key for a provider and return its refreshed inventory row.")


class ModelDisconnectParams(Params):
    slug: str
    session_id: str | None = None


class ModelDisconnectResult(Result):
    slug: str
    name: str
    disconnected: bool


method("model.disconnect", params=ModelDisconnectParams, result=ModelDisconnectResult,
       doc="Remove every credential (env keys and OAuth state) for a provider.")


# ── vault (methods_vault) ─────────────────────────────────────────────────────────────────────


class VaultKind(WireEnum):
    login = "login"
    payment = "payment"
    address = "address"


class VaultItem(Result):
    """Metadata-only view (``VaultItemMeta.to_dict`` + ``backend``); never a secret."""

    id: str
    kind: str
    label: str
    origin: str | None = None
    created_at: str = ""
    identifier: str | None = None
    identifier_type: str | None = None
    has_otp: bool | None = None
    backend: str


class VaultListResult(Result):
    items: list[VaultItem] = Field(default_factory=list)


method("vault.list", params=ProfileParams, result=VaultListResult,
       doc="Metadata-only listing across the local vault and every unlocked password manager.")


class VaultSource(Result):
    name: str
    display_name: str
    enabled: bool
    needs_unlock: bool
    unlocked: bool
    installed: bool


class VaultSourcesResult(Result):
    sources: list[VaultSource] = Field(default_factory=list)


method("vault.sources", params=ProfileParams, result=VaultSourcesResult,
       doc="Status of every login source (local vault + detected password managers).")


class VaultSourceSetParams(ProfileParams):
    name: str | None = None
    enabled: bool | None = None


class VaultSourceSetResult(Result):
    name: str
    enabled: bool


method("vault.source.set", params=VaultSourceSetParams, result=VaultSourceSetResult,
       doc="Enable or disable an external password manager (disabling also locks it).")


class VaultUnlockParams(ProfileParams):
    """The master password is consumed by the manager CLI and never stored or logged."""

    name: str | None = None
    password: str | None = None


class VaultUnlockResult(Result):
    name: str
    unlocked: bool = True


method("vault.unlock", params=VaultUnlockParams, result=VaultUnlockResult,
       doc="Unlock a password manager for this session with its master password.")


class VaultLockParams(ProfileParams):
    name: str | None = None


class VaultLockResult(Result):
    locked: bool = True


method("vault.lock", params=VaultLockParams, result=VaultLockResult,
       doc="Forget a manager's session token (every manager when no name is given).")


class VaultAddParams(ProfileParams):
    """``secret`` goes straight into the encrypted store; the result carries only the new id."""

    kind: VaultKind | None = None
    label: str | None = None
    origin: str | None = None
    secret: dict[str, JsonValue] | None = None


class VaultAddResult(Result):
    id: str


method("vault.add", params=VaultAddParams, result=VaultAddResult,
       doc="Add a login / payment / address item to the local vault.")


class VaultRemoveParams(ProfileParams):
    id: str | None = None


class VaultRemoveResult(Result):
    removed: bool


method("vault.remove", params=VaultRemoveParams, result=VaultRemoveResult,
       doc="Remove a local vault item by id.")


# ── foreign histories (methods_session_foreign) ───────────────────────────────────────────────


# ── subagents (methods_subagents) ─────────────────────────────────────────────────────────────


class SubagentSnapshot(Result):
    """``methods_subagents._SUBAGENT_SNAPSHOT_FIELDS`` projection of one live child record."""

    subagent_id: str
    parent_id: str | None = None
    depth: int | None = None
    goal: str | None = None
    delegation_id: str | None = None
    model: str | None = None
    started_at: float | None = None
    status: SubagentStatus | None = None
    tool_count: int | None = None
    last_tool: str | None = None
    accepting_steer: bool | None = None


class FailedDelegation(Result):
    """``async_delegation.failed_delegations_for_session`` row: one failed task of an async delegation."""

    delegation_id: str
    task_index: int = 0
    status: str
    goal: str = ""
    error: str | None = None
    dispatched_at: float | None = None
    completed_at: float | None = None


class SubagentListResult(Result):
    """``delegations``: recently failed async delegation tasks for the session (durable store), newest first."""

    subagents: list[SubagentSnapshot] = Field(default_factory=list)
    delegations: list[FailedDelegation] = Field(default_factory=list)


method("subagent.list", params=SessionParams, result=SubagentListResult,
       doc="Live children owned by this session (other sessions' children never leak).")


class SubagentIdParams(SessionParams):
    subagent_id: str


class SubagentInterruptResult(Result):
    found: bool
    subagent_id: str


method("subagent.interrupt", params=SubagentIdParams, result=SubagentInterruptResult,
       doc="Hard-interrupt one owned child; ``found`` is false when it already finished.")


class SubagentTailResult(Result):
    """``available`` is false while the child has no live transcript yet (or it was cleaned up)."""

    subagent_id: str
    available: bool = False
    text: str = ""
    truncated: bool = False


method("subagent.tail", params=SubagentIdParams, result=SubagentTailResult,
       doc="Last 16KB of an owned child's live transcript.")
