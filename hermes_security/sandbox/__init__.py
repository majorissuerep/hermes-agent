"""OS-native, default-deny sandbox for everything the model runs.

Hermes itself (vault keys, encrypted state, provider credentials, the agent loop) stays
unconfined. Every process the model causes — terminal commands, file-tool shell
operations, background processes, ``execute_code`` kernels, and the same for every
subagent — is spawned inside a kernel-enforced sandbox built from the session's grants:

- Linux: Landlock (filesystem allow-list) + seccomp (sockets, keyring, io_uring), via
  :mod:`hermes_security.sandbox.launcher` — unprivileged, inherited, irrevocable.
- macOS: Seatbelt (``sandbox-exec``) profile, :mod:`hermes_security.sandbox.seatbelt`.

Model tools are default-deny too: a session exposes only the tools its policy allows
(:mod:`hermes_security.sandbox.tool_policy`). Grants are per session
(:mod:`hermes_security.sandbox.session`), seeded from ``sandbox:`` in config.yaml and
presets (:mod:`hermes_security.sandbox.policy`); granting a path first scans it for
secrets (:mod:`hermes_security.sandbox.scan`).
"""
