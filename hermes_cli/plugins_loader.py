"""Plugin loading: directory/entry-point module import, deferred bundled platforms, portable packages,
dependency/config-schema warnings. Mixed into :class:`hermes_cli.plugins.PluginManager`.

Origin-internal names (``PluginContext``, ``LoadedPlugin``, ``_PLUGINS_DEBUG`` …) are imported lazily
through ``hermes_cli.plugins`` so tests that patch them on the origin keep working.
"""

from __future__ import annotations

import contextvars
import hashlib
import importlib
import importlib.metadata
import importlib.util
import logging
import re
import sys
import threading
import types
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Union

from hermes_constants import get_hermes_home, reset_hermes_home_override, set_hermes_home_override
from registration_lifecycle import replacement_coordinator
from hermes_cli.plugins_discovery import ENTRY_POINTS_GROUP, _select_entry_point_group
from hermes_cli.plugins_manifest import PluginManifest, manifest_key, portable_mcp_server_name, validate_config_schema
from hermes_cli.plugins_state import _plugin_settings_entry

if TYPE_CHECKING:  # pragma: no cover
    from hermes_cli.plugins import LoadedPlugin, PluginContext

logger = logging.getLogger("hermes_cli.plugins")

_NS_PARENT = "hermes_plugins"
_MODULE_NAMESPACE_LOCK = threading.RLock()
_BARE_MODULE_SCOPE: dict[str, str] = {}  # bare module name -> owning scope_key

# Per-plugin deadline on import + register(): ``plugins.load_timeout_seconds`` (default 10s, 0 disables,
# clamped to the max). A plugin that never returns is skipped with a named reason and loading moves on
# (#108139). Python cannot kill a thread, so the worker is abandoned as a daemon; the cap bounds how many
# abandoned loaders one process may accumulate (#98382) — past it, further loads are refused, not run inline.
_LOAD_TIMEOUT_SECS = 10.0
_MAX_LOAD_TIMEOUT_SECS = 600.0
_MAX_ABANDONED_LOADERS = 8
_LOADER_THREAD_PREFIX = "plugin-load:"  # names each deadline-bounded load worker; the re-arm guard matches it
_ABANDONED_LOADERS: list[threading.Thread] = []
_ABANDONED_LOADERS_LOCK = threading.Lock()
# PluginContexts of the outermost deadline-bounded load; set only inside its worker.
_IN_PLUGIN_LOAD: contextvars.ContextVar[Optional[list[PluginContext]]] = contextvars.ContextVar(
    "hermes_plugin_load_scope", default=None,
)


class PluginLoadTimeout(Exception):
    """Raised on the loading thread when a plugin's import + ``register()`` overran its deadline."""


def in_plugin_load_worker() -> bool:
    """True on a deadline worker thread; re-entrant discovery from there must not block on its own parent."""
    return _IN_PLUGIN_LOAD.get() is not None


def _resolve_plugin_load_timeout() -> float:
    """Effective per-plugin load deadline from ``plugins.load_timeout_seconds`` (default 10s; ``0`` runs
    loads inline with no deadline; clamped to ``_MAX_LOAD_TIMEOUT_SECS``)."""
    default = _LOAD_TIMEOUT_SECS
    try:
        from hermes_cli.config import load_config_readonly
        plugins_cfg = (load_config_readonly() or {}).get("plugins")
        if not isinstance(plugins_cfg, dict) or plugins_cfg.get("load_timeout_seconds") is None:
            return default
        timeout = float(plugins_cfg["load_timeout_seconds"])
    except (TypeError, ValueError):
        logger.warning("plugins.load_timeout_seconds is not a number; using default %gs", default)
        return default
    except Exception:
        return default
    if timeout < 0:
        logger.warning("plugins.load_timeout_seconds=%g is negative; using default %gs", timeout, default)
        return default
    if timeout > _MAX_LOAD_TIMEOUT_SECS:
        logger.warning("plugins.load_timeout_seconds=%g exceeds max %gs; clamping", timeout,
                       _MAX_LOAD_TIMEOUT_SECS)
        return _MAX_LOAD_TIMEOUT_SECS
    return timeout


def _reserve_abandoned_loader_slot() -> None:
    """Drop finished abandoned loaders; refuse the load once the live cap is reached. Refusing beats
    loading inline: at the cap the process already holds several hung loaders, so an inline load is the
    exact startup hang this deadline exists to prevent."""
    with _ABANDONED_LOADERS_LOCK:
        _ABANDONED_LOADERS[:] = [t for t in _ABANDONED_LOADERS if t.is_alive()]
        if len(_ABANDONED_LOADERS) < _MAX_ABANDONED_LOADERS:
            return
    raise PluginLoadTimeout(
        f"not loaded: {_MAX_ABANDONED_LOADERS} abandoned plugin loader thread(s) are still running "
        f"(plugins.load_timeout_seconds); restart Hermes to retry"
    )


def run_with_load_deadline(plugin_key: str, ctx: PluginContext, fn: Callable[[], Any]) -> Any:
    """Run ``fn`` (a plugin's import + ``register()``) under the per-plugin deadline.

    The worker inherits the caller's context (the Hermes-home override is a ContextVar). On timeout the
    worker is abandoned as a daemon, ``ctx`` is marked so any registration it still attempts is ignored,
    and :class:`PluginLoadTimeout` is raised on the calling thread so the usual failure path records the
    reason and disposes whatever was registered before the hang.
    """
    # Re-entrant load on our own deadline worker: run inline; the outer deadline covers it.
    outer_scope = _IN_PLUGIN_LOAD.get()
    if outer_scope is not None:
        outer_scope.append(ctx)
        try:
            return fn()
        finally:
            outer_scope.remove(ctx)
    timeout = _resolve_plugin_load_timeout()
    if timeout <= 0:
        return fn()
    _reserve_abandoned_loader_slot()
    outcome: list[Any] = []
    failure: list[BaseException] = []

    scope = [ctx]

    def _worker() -> None:
        token = _IN_PLUGIN_LOAD.set(scope)
        try:
            outcome.append(fn())
        except BaseException as exc:  # re-raised on the loading thread, KeyboardInterrupt included
            failure.append(exc)
        finally:
            _IN_PLUGIN_LOAD.reset(token)

    worker = threading.Thread(
        target=contextvars.copy_context().run, args=(_worker,), name=f"{_LOADER_THREAD_PREFIX}{plugin_key}", daemon=True,
    )
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        for loaded_ctx in scope:
            loaded_ctx._abandon_load()
        with _ABANDONED_LOADERS_LOCK:
            _ABANDONED_LOADERS.append(worker)
        raise PluginLoadTimeout(f"load timed out after {timeout:g}s (import + register() never returned)")
    if failure:
        raise failure[0]
    return outcome[0]


def _evict_modules(module_name: str) -> None:
    """Drop ``module_name`` and every ``module_name.*`` submodule from ``sys.modules``."""
    prefix = f"{module_name}."
    for name in [n for n in sys.modules if n == module_name or n.startswith(prefix)]:
        del sys.modules[name]


def _serialized_replacement(method):
    """Make snapshot → write → lease attachment one atomic transaction."""
    @wraps(method)
    def wrapped(*args, **kwargs):
        with replacement_coordinator.transaction():
            return method(*args, **kwargs)

    return wrapped


@contextmanager
def _plugin_home_scope(home: Path):
    """Bind discovery and loading to the manager's immutable Hermes home."""
    token = set_hermes_home_override(home)
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def _load_error_text(exc: BaseException) -> str:
    """Human-readable load failure; ``sys.exit(0)`` has an empty ``str()`` so name the class and code."""
    if isinstance(exc, SystemExit):
        return f"SystemExit({exc.code!r}) raised during import/register()"
    return str(exc)


def _dist_installed(req: str) -> Optional[bool]:
    """Best-effort presence probe on a requirement's distribution name; ``None`` when unprobeable."""
    dist = re.split(r"[<>=!~\[;\s]", req, maxsplit=1)[0].strip()
    if not dist:
        return None
    try:
        importlib.metadata.version(dist)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False
    except Exception:
        return None


class PluginLoaderMixin:
    def on_plugin_loaded(self, callback: Callable[[list[dict[str, Any]]], Any]) -> Callable[[], None]:
        """Subscribe to "a discovery sweep loaded plugins this process did not have": fires from INSIDE
        :meth:`discover_and_load` (never emitted by an install RPC) with one
        ``{name, key, activated_now, deferred}`` summary per NEWLY loaded plugin — every plugin at boot,
        just the newcomer after a mid-run ``hermes plugins install/enable``, Desktop / dashboard /
        ``plugins.manage`` install-enable-update, a tool-triggered force re-discovery or the gateway's
        ``reload-plugins`` verb (all of which run ``discover_plugins(force=True)``; a non-forced call
        short-circuits on ``_discovered`` and never fires). See
        :func:`hermes_cli.plugins_activation.plugin_activation_summary` for the payload: ``activated_now``
        (gateway commands/transforms/hooks/callbacks, live at once) vs ``deferred`` (``tools``/``prompt``
        until the next session, ``mcp_servers`` — the plugin's mcp.json server names — until ``mcp.reload``).
        Listeners belong to the process (gateway runner, TUI server), not to a plugin, so ``unload()``
        never clears them. Returns an unsubscribe callable. Fires on the discovering thread with the
        discovery lock released; marshal onto your own loop."""
        if not callable(callback):
            raise ValueError("on_plugin_loaded requires a callable")
        listeners = self._plugin_loaded_listeners
        listeners.append(callback)

        def _unsubscribe() -> None:
            try:
                listeners.remove(callback)
            except ValueError:
                pass
        return _unsubscribe

    def _notify_plugin_loaded(self, loaded_before: frozenset) -> None:
        """Fire every :meth:`on_plugin_loaded` listener for the plugins this sweep added over
        ``loaded_before``; nothing new = no event. One raising listener never starves the rest."""
        if not self._plugin_loaded_listeners:
            return
        from hermes_cli.plugins_activation import activation_summaries
        summaries = [s for s in activation_summaries(self) if s["key"] not in loaded_before]
        if not summaries:
            return
        for callback in list(self._plugin_loaded_listeners):
            try:
                callback(summaries)
            except Exception:
                logger.warning("plugin-loaded listener %r raised", callback, exc_info=True)






    def _warn_python_dependencies(self, manifest: PluginManifest) -> None:
        """Report missing dependencies without installing during discovery.

        Plugin admission and PM repair own dependency changes.
        """
        deps = manifest.python_dependencies
        if not deps:
            return
        key = manifest_key(manifest)
        missing = [req for req in deps if _dist_installed(req) is False]
        if missing:
            logger.warning(
                "Plugin %s declares Python dependencies that are not "
                "installed: %s. For an enabled plugin, run hermes pm repair, "
                "then restart Hermes. Discovery does not install dependencies.",
                key, ", ".join(missing),
            )
        else:
            logger.debug("Plugin %s python_dependencies satisfied: %s", key, ", ".join(deps))

    def _validate_plugin_config_schema(self, manifest: PluginManifest) -> None:
        """Warn (never block) on plugins.entries.<id> settings that violate config_schema.

        See #64165.
        """
        if not manifest.config_schema:
            return
        plugin_id = manifest_key(manifest)
        settings: Mapping[str, Any] = {}
        try:
            from hermes_cli.config import load_config
            entry = _plugin_settings_entry(load_config() or {}, plugin_id) or {}
            raw = entry.get("settings")
            if not isinstance(raw, Mapping):
                raw = entry.get("config")  # migration fallback mirroring ctx.get_config
            settings = raw if isinstance(raw, Mapping) else {}
        except Exception:
            settings = {}
        for warning in validate_config_schema(plugin_id, manifest.config_schema, settings):
            logger.warning("Plugin %s config: %s", plugin_id, warning)

    def _load_plugin(self, manifest: PluginManifest) -> None:
        """Import a plugin module and call its ``register(ctx)`` function."""
        with self._discovery_lock, _plugin_home_scope(self.home_path):
            self._load_plugin_scoped(manifest)

    def _load_plugin_scoped(self, manifest: PluginManifest) -> None:
        """Load one plugin with the manager's home bound as current."""
        from hermes_cli.plugins import LoadedPlugin, PluginContext, _PLUGINS_DEBUG
        loaded = LoadedPlugin(manifest=manifest)
        plugin_key = manifest_key(manifest)
        logger.debug(
            "Loading plugin '%s' (source=%s, kind=%s, path=%s)",
            plugin_key, manifest.source, manifest.kind, manifest.path,
        )
        if manifest.portable:
            self._load_portable_plugin(manifest, loaded)
            return
        # requires_hermes gate: skip cleanly (no import, no traceback) on a version mismatch.
        from hermes_cli.plugins_manifest import requires_hermes_error
        reason = requires_hermes_error(manifest)
        if reason:
            loaded.error = reason
            logger.warning("Plugin '%s' skipped: %s", plugin_key, reason)
            self._plugins[plugin_key] = loaded
            return
        registration_start = len(self._registration_order)
        module_name = self._policy_module_name(manifest)
        self._track_tool_override_policy(manifest, module_name)
        ctx = PluginContext(manifest, self)
        in_host = self._runs_in_plugin_host(manifest)

        def _import_and_register() -> bool:
            """Import + register() — the part a plugin controls, so the part the deadline covers."""
            # Declared language packs register before any plugin code runs, inside the same ledger slice
            # so a failing register() unwinds them too.
            self._register_declared_locales(manifest, ctx)
            if in_host and not self._is_manifest_only_language_pack(manifest):
                self._plugin_host().load(manifest, ctx, module_name=module_name,
                                         entrypoint=manifest.source not in {"user", "project"})
                return True
            # Reuse a deferred platform's already-imported package so its body doesn't run twice.
            # See #78050.
            module = self._predeclared_modules.pop(plugin_key, None)
            if module is None and manifest.source in {"user", "project", "bundled"}:
                if self._is_manifest_only_language_pack(manifest):
                    return True  # pure pack: locales/ is the whole plugin, no register() to run
                module = self._load_directory_module(manifest, module_name=module_name)
            elif module is None:
                module = self._load_entrypoint_module(manifest)
            register_fn = None
            if module is not None and not isinstance(module, types.ModuleType) and callable(module):
                # An entry point declared as ``module:function`` resolves to the function object itself via
                # ``ep.load()``, not its module (#72052).
                register_fn = module
                module = sys.modules.get(getattr(register_fn, "__module__", ""))
            loaded.module = module
            if register_fn is None:
                register_fn = getattr(module, "register", None)
            if register_fn is None:
                loaded.error = "no register() function"
                logger.warning("Plugin '%s' has no register() function", manifest.name)
                return False
            register_fn(ctx)
            return True

        try:
            if run_with_load_deadline(plugin_key, ctx, _import_and_register):
                self._attribute_registrations(loaded, plugin_key, registration_start)
                loaded.enabled = True
                from hermes_cli.plugins_ledger import _hook_source_of

                self._drop_fallback_hooks(_hook_source_of(manifest.name, loaded.module))
        except (Exception, SystemExit) as exc:
            # SystemExit too: a plugin module with an unguarded ``main()``/``sys.exit()`` must not take the
            # whole process (and every other plugin's registry) down with it; KeyboardInterrupt still propagates.
            # PluginLoadTimeout lands here as well: the abandoned worker's later registrations are refused
            # by ``ctx``, and whatever it registered before hanging is disposed below.
            owned = [r for r in self._registration_order if r.plugin_key == plugin_key]
            self._dispose_registrations(owned)
            self._forget_registrations(owned)
            loaded.error = _load_error_text(exc)
            # register() may have subscribed before raising; a failed plugin must leave no callable reachable
            # from later event dispatch.
            self._remove_plugin_subscriptions(plugin_key)
            logger.warning("Failed to load plugin '%s': %s", manifest.name, _load_error_text(exc), exc_info=_PLUGINS_DEBUG)
        # The failure path swept this plugin's whole ledger (not just the registration_start slice), so
        # discovery-time pre-registrations are gone too.
        # There is no live tool left to credit — attribution and the registry agree at zero. Only the
        # success path pops _predeclared_tools, so drop the entry here rather than let the bookkeeping
        # outlive the load attempt (#78050).
        if not loaded.enabled:
            self._predeclared_tools.pop(plugin_key, None)
        self._plugins[plugin_key] = loaded

    @staticmethod
    def _is_manifest_only_language_pack(manifest: PluginManifest) -> bool:
        """A ``provides_locales`` plugin with no ``__init__.py`` is complete without Python — like a
        manifest-only desktop plugin, it loads from its declared files alone."""
        return bool(manifest.provides_locales and manifest.path
                    and not (Path(manifest.path) / "__init__.py").is_file())

    def _register_declared_locales(self, manifest: PluginManifest, ctx) -> None:
        """``provides_locales`` -> ``ctx.register_locale_dir(<plugin>/locales)``; a declared id with no file
        is a warning (the pack promised a language it does not ship), never a load failure."""
        if not manifest.provides_locales or not manifest.path:
            return
        locales_dir = Path(manifest.path) / "locales"
        handles = ctx.register_locale_dir(locales_dir, metadata=manifest.locale_metadata)
        registered = {handle.key.split(".", 1)[0] for handle in handles}
        for lang_id in manifest.provides_locales:
            if lang_id not in registered:
                logger.warning("Plugin '%s' declares provides_locales %r but %s has no %s.yaml",
                               manifest.name, lang_id, locales_dir, lang_id)

    def _runs_in_plugin_host(self, manifest: PluginManifest) -> bool:
        """``plugins.isolation: host`` sends every non-bundled Python plugin to the profile's host."""
        from hermes_cli.plugin_isolation import ISOLATION_HOST, isolation_mode
        return manifest.source != "bundled" and isolation_mode() == ISOLATION_HOST

    def _plugin_host(self) -> Any:
        """This manager's plugin host (one process per profile home), created on first use."""
        host = getattr(self, "_plugin_host_instance", None)
        if host is None:
            from hermes_cli.plugin_host import PluginHost
            host = self._plugin_host_instance = PluginHost(self)
        return host

    def _track_tool_override_policy(self, manifest: PluginManifest, module_name: str) -> None:
        """Install the plugin's tool-override policy in tools.registry as a ledger-owned lease."""
        from hermes_cli.plugins import PluginContext
        from tools.registry import registry as _registry
        scope = self.scope_key
        with replacement_coordinator.transaction():
            previous_policy = _registry.snapshot_plugin_override_policy(module_name, scope=scope)
            current_policy = _registry.register_plugin_override_policy(
                module_name, PluginContext(manifest, self)._tool_override_allowed(""), scope=scope,
            )
            policy_lease = replacement_coordinator.acquire(
                ("tool_override_policy", scope, module_name), current=current_policy,
                previous=previous_policy,
                restore=lambda replacement: _registry.restore_plugin_override_policy(
                    module_name, current_policy, replacement, scope=scope,
                ),
            )
            self._track_registration(manifest, "tool_override_policy", module_name, policy_lease.dispose)

    def _attribute_registrations(
        self, loaded: LoadedPlugin, plugin_key: str, registration_start: int
    ) -> None:
        """Fill ``loaded.*_registered`` from the ledger slice this plugin's register() produced."""
        registrations = [
            r for r in self._registration_order[registration_start:]
            if r.plugin_key == plugin_key and r.active
        ]

        def _keys(kind: str) -> list[str]:
            return [r.key for r in registrations if r.kind == kind]

        # Discovery-time tools predate registration_start; credit them back or `hermes plugins list`
        # under-reports once the deferred adapter materializes.
        predeclared = [t for t in self._predeclared_tools.pop(plugin_key, []) if t in self._plugin_tool_names]
        loaded.tools_registered = predeclared + [k for k in _keys("tool") if k not in predeclared]
        loaded.hooks_registered = _keys("hook")
        loaded.middleware_registered = _keys("middleware")
        loaded.commands_registered = _keys("command")
        logger.debug(
            "  registered: %d tool(s), %d hook(s), %d middleware, %d slash command(s), %d CLI command(s)",
            len(loaded.tools_registered), len(loaded.hooks_registered),
            len(loaded.middleware_registered), len(loaded.commands_registered),
            sum(1 for c in self._cli_commands if c in _keys("cli_command")),
        )

    def _load_portable_plugin(self, manifest: PluginManifest, loaded: LoadedPlugin) -> None:
        """Load validated portable components without importing Python code."""
        from hermes_cli.plugins import PluginContext
        lookup_key = manifest_key(manifest)
        try:
            from hermes_cli.agent_plugins import load_agent_plugin
            package = load_agent_plugin(
                Path(manifest.path), get_hermes_home() / "plugin-data" / manifest.skill_namespace)
            ctx = PluginContext(manifest, self)
            for diagnostic in package.diagnostics:
                logger.warning("Agent Plugin '%s' [%s]: %s", lookup_key, diagnostic.scope, diagnostic.message)
            for skill in package.skills:
                try:
                    ctx.register_skill(skill.name, skill.skill_md, skill.description, skill.frontmatter)
                except Exception as exc:
                    logger.warning("Agent Plugin '%s' skill '%s' skipped: %s", lookup_key, skill.name, exc)
            from hermes_cli.agent_plugins import _clear_liveness, _set_liveness
            from hermes_platform import declaration
            registered: list[str] = []
            try:
                for server_name, config in package.mcp_servers.items():
                    internal_name = portable_mcp_server_name(lookup_key, server_name)
                    if internal_name in self._portable_mcp_servers:
                        logger.warning("Agent Plugin '%s' MCP server '%s' skipped: name already taken by plugin '%s'; rename one server",
                                       lookup_key, internal_name, self._portable_mcp_server_plugins.get(internal_name, "?"))
                        continue
                    self._portable_mcp_servers[internal_name] = dict(config)
                    self._portable_mcp_server_plugins[internal_name] = lookup_key
                    server_decl = package.server_declarations.get(server_name)
                    if server_decl is not None:
                        declaration.register(internal_name, server_decl.declaration)
                        _set_liveness(internal_name, server_decl.liveness)
                    registered.append(internal_name)
                for internal_name in registered:
                    def release(name: str = internal_name) -> None:
                        self._portable_mcp_servers.pop(name, None)
                        self._portable_mcp_server_plugins.pop(name, None)
                        declaration.unregister(name)
                        _clear_liveness(name)

                    self._track_registration(manifest, "portable_mcp", internal_name, release)
                loaded.enabled = True
            except BaseException:
                for internal_name in registered:
                    self._portable_mcp_servers.pop(internal_name, None)
                    self._portable_mcp_server_plugins.pop(internal_name, None)
                    declaration.unregister(internal_name)
                    _clear_liveness(internal_name)
                raise
        except (Exception, SystemExit) as exc:
            loaded.error = _load_error_text(exc)
            logger.warning("Agent Plugin '%s' disabled: %s", lookup_key, loaded.error)
        self._plugins[lookup_key] = loaded

    def _directory_module_name(self, manifest: PluginManifest) -> str:
        """Profile-safe import namespace for a directory plugin: the bare ``hermes_plugins.<slug>`` for the
        first scope that claims it, a ``__home_<digest>`` suffix for any other scope."""
        slug = manifest_key(manifest).replace("/", "__").replace("-", "_")
        bare_name = f"{_NS_PARENT}.{slug}"
        with _MODULE_NAMESPACE_LOCK:
            if _BARE_MODULE_SCOPE.setdefault(bare_name, self.scope_key) == self.scope_key:
                return bare_name
            digest = hashlib.sha256(self.scope_key.encode("utf-8")).hexdigest()[:12]
            return f"{bare_name}__home_{digest}"

    def _policy_module_name(self, manifest: PluginManifest) -> str:
        """Return the module prefix whose callbacks inherit plugin policy."""
        if manifest.source == "entrypoint" and manifest.path:
            module_name = str(manifest.path).partition(":")[0].strip()
            if module_name:
                return module_name
        return self._directory_module_name(manifest)

    def _load_directory_module(
        self, manifest: PluginManifest, *, module_name: Optional[str] = None,
    ) -> types.ModuleType:
        """Import a directory plugin as ``hermes_plugins.<slug>`` (slug from ``manifest.key`` so
        ``image_gen/openai`` cannot collide with ``tts/openai``)."""
        plugin_dir = Path(manifest.path)  # type: ignore[arg-type]
        init_file = plugin_dir / "__init__.py"
        if not init_file.exists():
            raise FileNotFoundError(f"No __init__.py in {plugin_dir}")
        if _NS_PARENT not in sys.modules:
            ns_pkg = types.ModuleType(_NS_PARENT)
            ns_pkg.__path__ = []  # type: ignore[attr-defined]
            ns_pkg.__package__ = _NS_PARENT
            sys.modules[_NS_PARENT] = ns_pkg
        module_name = module_name or self._directory_module_name(manifest)
        # Evict stale entries for this slug (same slug cached from another Hermes home, or an earlier force
        # reload). Replacing only sys.modules[module_name] is not enough: the plugin's relative imports are
        # cached as "module_name.sub" and resolve from sys.modules first, so a stale submodule would keep
        # serving the previous load's code/state.
        _evict_modules(module_name)
        spec = importlib.util.spec_from_file_location(
            module_name, init_file, submodule_search_locations=[str(plugin_dir)])
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {init_file}")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = module_name
        module.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            # Don't leave a half-initialized module (or its partially imported relative submodules) cached — a
            # retry or a same-slug plugin in another profile would inherit broken state.
            _evict_modules(module_name)
            raise
        return module

    def _load_entrypoint_module(self, manifest: PluginManifest) -> types.ModuleType | Callable[..., Any]:
        """Load a pip-installed plugin via its entry-point reference: the module for a bare ``module`` target,
        the referenced attribute (normally ``register``) for the ``module:function`` form."""
        for ep in _select_entry_point_group(importlib.metadata.entry_points(), ENTRY_POINTS_GROUP):
            if ep.name == manifest.name:
                return ep.load()
        raise ImportError(f"Entry point '{manifest.name}' not found in group '{ENTRY_POINTS_GROUP}'")
