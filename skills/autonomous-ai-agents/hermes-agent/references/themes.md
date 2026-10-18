# TUI themes and skins

A skin is a semantic palette resolved by `hermes_cli/skin_engine.py` for the local
terminal UI. There is no Desktop or multi-surface gateway fanout.

1. Inspect `templates/skin.yaml` and existing profile-local skins.
2. Choose a lowercase, hyphen-safe name and a readable palette, including error,
   warning, selection and diff contrast.
3. Save the skin under `<hermes-home>/skins/<name>.yaml` using the installed
   skin tools and state writers appropriate to its storage policy.
4. Activate with `hermes skin --help` / `/skin`; verify the actual TUI rendering.

Use `hermes config set display.skin NAME` rather than writing `config.yaml`.
Do not truncate encrypted settings or claim a live repaint from file existence.
The current profile owns both the skin and selection. Keep the default palette
as fallback for unspecified semantic tokens. A theme change must not rebuild the
conversation system prompt or alter model tools.
