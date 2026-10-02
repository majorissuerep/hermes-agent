---
name: graphify
description: Reuse knowledge graphs for code and document questions.
version: 1.0.0
author: Luoman + Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Knowledge Graph, Codebase, Documents, Repeated Questions]
    category: software-development
    requires_tools: [terminal, read_file, write_file]
---

# Graphify Skill

Build and reuse a local knowledge graph of a codebase or document collection.
Graphify parses code; Hermes extracts document concepts using the current
session. Graphs guide source selection and preserve relationships across requests.

## When to Use

Use proactively when repeated questions, ongoing development, architecture
analysis, or follow-up research on the same collection are expected. Load this
skill before broad exploration, build once, and query before rereading the
collection. An existing project graph is useful even for a single question.
Skip a new index for a small one-off lookup or an explicit user opt-out.

## Prerequisites

Graphify's pinned Python runtime ships with Hermes. Use `terminal` to run
`hermes graphify`, and `read_file` / `write_file` for document extraction. No
Graphify account or separate model credential is needed. The index belongs to
the selected project directory on the terminal execution target.

## How to Run

Substitute the corpus directory for `<project>` and quote paths. The command
uses Hermes' own Python environment, so the terminal's Python needs no setup.
With a remote terminal, run it on the execution target; if that target lacks
Hermes, report it and continue using source tools.

```text
hermes graphify scan "<project>"
hermes graphify build "<project>"
hermes graphify build "<project>" --semantic "<batch.json>"
hermes graphify query "<project>" "How does authentication work?"
hermes graphify explain "<project>" "validate_token"
hermes graphify path "<project>" "login" "validate_token"
```

## Quick Reference

| Operation | Result |
|---|---|
| `scan` | Freshness, source change counts, next 20 pending documents and hashes |
| `build` | Cached AST + current document cache → `graph.json` and `GRAPH_REPORT.md` |
| `build --semantic` | Cache a document batch, then rebuild the combined graph |
| `query` | Scoped traversal, bounded to 2,000 tokens by default (`--budget N`) |
| `explain` / `path` | Symbol neighbors or connections between concepts |

## Procedure

1. Choose the repository or document collection root explicitly. Run `scan`.
   Reuse a fresh graph. For an existing graph with unknown freshness, query for
   orientation and verify relevant sources; rebuild when a refresh is needed.
2. If the graph is missing or stale, run `build`. Code extraction is local and
   cached by Graphify. Respect `.gitignore` and `.graphifyignore`; generated
   graphs and credentials are excluded from the corpus.
3. For pending documents, load [references/document-extraction.md](references/document-extraction.md).
   Read the listed sources using `read_file`, extract a bounded batch into JSON
   with `write_file`, then run `build --semantic`. This reuses the current
   session model, including for PDFs and Office files supported by `read_file`.
   Repeat for pending files needed by the task. Report partial document coverage.
4. Run `query` with the actual question and an explicit project root. Follow
   the returned source paths with `read_file`; use `search_files` for uncovered
   symbols or a query with no useful match. Distinguish `EXTRACTED` relationships
   from `INFERRED` ones, and cite source locations when answering.
5. After source edits, rerun `scan` and refresh before another graph query.
   Changed document entries become cache misses; deleted sources disappear on
   rebuild. Preserve the project index for future requests and sessions.

## Pitfalls

- A missing graph match does not prove that a symbol or relationship is absent.
  Confirm in source, especially when document coverage is incomplete.
- A document-only corpus needs a semantic batch before its first graph can be
  built. `scan` still works and lists the files to read.
- A build refuses an empty graph or an unexpected reduction in nodes. Use
  `--force` only after confirming intended source deletions or reduced scope.
- Keep document batches inside `graphify-out/` so they are not indexed as input.
  Use the helper rather than Graphify's assistant installer: Hermes owns skill
  installation and prompt state.
- Never index an entire home directory, Hermes state, or credential collections.
  Index only the project or document scope needed for the user's work.

## Verification

Run `scan` after building. Confirm `graph_exists`, `freshness: fresh`, and the
reported document coverage. Query a known symbol or document concept and check
its source with `read_file`. If a build fails, keep working with source tools
and report the failure without presenting the graph as current.
