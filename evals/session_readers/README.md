# Native session reader A/B probes

`search_readonly_ab.py` exercises real session-search readers in disposable homes.
`session_readers_cli_ab.py` checks public CLI insights/session readers against empty
and populated temporary profiles. Both require explicit baseline/fixed paths, a PM
interpreter and private output. They compare DB contents/hashes and report exact
source identity. These are targeted historical regressions, not a whole-product
acceptance receipt or live-provider qualification. Use each script's `--help`.
