# Contributing

Development targets **Linux and macOS**. Read [AGENTS.md](AGENTS.md) and the
area guide before editing. The [contribution guide](website/docs/developer-guide/contributing.md)
is the maintained reference for architecture, contribution scope, code style,
and review. Use [fork issues](https://github.com/majorissuerep/hermes-agent/issues)
for this fork; security reports follow [SECURITY.md](SECURITY.md).

Prioritize repair, simplification, and reliability within the existing product.
New default integrations must meet the [scope and qualification contract](website/docs/developer-guide/reliability.md#product-scope);
upstream availability alone is not a reason to ship them.

## Development setup

Select an isolated `HERMES_HOME` and `HERMES_RUNTIME_DIR` before starting Hermes.
Follow the [PM developer workflow](website/docs/reference/package-management.md#developer-workflow)
for the pinned Python and tools, then activate the checkout:

```sh
source ./activate          # fish: source ./activate.fish
hermes --version
```

Build a **separate, disposable** test environment with the prepared Python:

```sh
python -m pm.build_env --source . --out .venv --group dev --group test
scripts/run_tests.sh tests/agent/ --file-retries 0
python scripts/check
```

PM refuses an existing output. Do not modify managed environments with raw pip
or uv. For an external test environment, set `HERMES_PYTHON` to its interpreter.
Always use `scripts/run_tests.sh`; it isolates homes, credentials, and test files.
See [tests/AGENTS.md](tests/AGENTS.md) for behavioral tests and native OS markers.

For JavaScript work, use `npm ci` and the affected workspace checks. The website
has its own install: `npm ci --prefix website`.

## Before submitting

- Reproduce the behavior on current `main`; inspect its history and intent.
- Add a small behavioral regression test, red on the base, for each actual fix.
- Preserve prompt-cache prefixes, profile isolation, and encrypted user state.
- Run relevant checks without masking retries or skips; attach exact-head evidence.
- Rebase on `main` before merging. Keep user data and credentials out of artifacts.

Install, update, repair, and provider changes additionally follow the
[reliability contract](website/docs/developer-guide/reliability.md) and
[isolated lifecycle acceptance](scripts/e2e/README.md).
