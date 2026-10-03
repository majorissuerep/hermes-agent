#!/usr/bin/env python3
"""Project-local Graphify workflow using Hermes for semantic extraction."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

SPEC = Path(__file__).resolve().parents[1] / "references" / "document-extraction.md"


def _write_json(path: Path, data: dict) -> None:
    stream = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _corpus(root: Path) -> tuple[dict, Path, dict[str, str]]:
    from graphify.cache import file_hash
    from graphify.detect import detect
    from graphify.paths import GRAPHIFY_OUT

    if not root.is_dir():
        raise ValueError(f"Project directory does not exist: {root}")
    detection = detect(root, google_workspace=False, follow_symlinks=False)
    if detection["walk_errors"]:
        raise ValueError(f"Incomplete project scan: {detection['walk_errors']}")
    hashes = {
        Path(source).relative_to(root).as_posix(): file_hash(Path(source), root=root)
        for sources in detection["files"].values() for source in sources
    }
    return detection, root / GRAPHIFY_OUT, hashes


def _semantic(detection: dict, root: Path) -> tuple[list, list, list, list]:
    from graphify.cache import check_semantic_cache

    sources = [source for kind, files in detection["files"].items()
               if kind != "code" for source in files]
    return check_semantic_cache(sources, root=root, prompt_file=SPEC)


def _status(root: Path, detection: dict, output: Path, hashes: dict) -> dict:
    state_path = output / ".hermes-graphify.json"
    previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    graph_exists = (output / "graph.json").is_file()
    old_hashes = previous["source_hashes"] if previous else {}
    changed = sorted(source for source, digest in hashes.items() if old_hashes.get(source) != digest)
    deleted = sorted(set(old_hashes) - set(hashes))
    freshness = "missing" if not graph_exists else "unknown"
    if graph_exists and previous:
        freshness = "stale" if (
            changed or deleted or previous["extractor_version"] != version("graphifyy")
        ) else "fresh"
    _, _, _, pending = _semantic(detection, root)
    pending_relative = [Path(source).relative_to(root).as_posix() for source in pending]
    return {
        "project": str(root), "graph": str(output / "graph.json"),
        "graph_exists": graph_exists, "freshness": freshness,
        "changed_sources": changed[:50], "changed_source_count": len(changed),
        "deleted_sources": deleted[:50], "deleted_source_count": len(deleted),
        "source_hashes": {source: hashes[source] for source in pending_relative[:20]},
        "pending_documents": pending_relative[:20], "pending_document_count": len(pending),
        "files": {kind: len(files) for kind, files in detection["files"].items()},
    }


def scan(root: Path) -> dict:
    detection, output, hashes = _corpus(root)
    return _status(root, detection, output, hashes)


def _save_batch(path: Path, root: Path, detection: dict, hashes: dict, known_nodes: list) -> None:
    from graphify.cache import save_semantic_cache

    batch = json.loads(path.read_text(encoding="utf-8"))
    selected = batch.get("source_hashes")
    if not isinstance(selected, dict) or not selected:
        raise ValueError("Semantic batch needs source_hashes copied from scan.")
    documents = {Path(source).relative_to(root).as_posix()
                 for kind, sources in detection["files"].items() if kind != "code" for source in sources}
    for source, digest in selected.items():
        if source not in documents or hashes.get(source) != digest:
            raise ValueError(f"Semantic source is outside the corpus or changed since scan: {source}")
    replacing = {str(root / source) for source in selected} | set(selected)
    known_ids = {node["id"] for node in known_nodes if node.get("source_file") not in replacing}
    nodes, edges, hyperedges = (batch.get(key, []) for key in ("nodes", "edges", "hyperedges"))
    if not all(isinstance(items, list) for items in (nodes, edges, hyperedges)):
        raise ValueError("Semantic nodes, edges and hyperedges must be lists.")
    covered = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not node["id"]:
            raise ValueError("Each semantic node needs a nonempty string id.")
        if node["id"] in known_ids:
            raise ValueError(f"Semantic node id already belongs to another source: {node['id']}")
        known_ids.add(node["id"])
        covered.add(node.get("source_file"))
    if set(selected) - covered:
        raise ValueError("Every selected document needs at least one source-backed node.")
    for item in nodes + edges + hyperedges:
        if not isinstance(item, dict) or item.get("source_file") not in selected:
            raise ValueError("Every semantic item must name a selected project-relative source_file.")
    for edge in edges:
        if edge.get("source") not in known_ids or edge.get("target") not in known_ids:
            raise ValueError("Semantic edge endpoints must exist in the current extraction.")
        if edge.get("confidence") not in {"EXTRACTED", "INFERRED", "AMBIGUOUS"}:
            raise ValueError("Semantic edges need an explicit confidence tag.")
    for hyperedge in hyperedges:
        if not isinstance(hyperedge.get("nodes"), list) or not set(hyperedge["nodes"]) <= known_ids:
            raise ValueError("Semantic hyperedge members must exist in the current extraction.")
    # The upstream cache uses absolute source paths; the skill uses portable relative paths.
    for item in nodes + edges + hyperedges:
        item["source_file"] = str(root / item["source_file"])
    save_semantic_cache(nodes, edges, hyperedges, root=root, prompt_file=SPEC,
                        allowed_source_files=[root / source for source in selected])


def build(root: Path, semantic: Path | None = None, *, force: bool = False) -> dict:
    from graphify.analyze import god_nodes, surprising_connections
    from graphify.build import build_from_json
    from graphify.cluster import cluster, score_all
    from graphify.export import to_json
    from graphify.extract import collect_files, extract
    from graphify.report import generate

    detection, output, hashes = _corpus(root)
    # Detection applies the credential filter as well as project ignore rules.
    code_sources = set(detection["files"]["code"])
    code = [path for path in collect_files(root) if str(path) in code_sources]
    ast = extract(code, root=root, cache_root=root, parallel=False) if code else {"nodes": [], "edges": []}
    cached_nodes, cached_edges, cached_hyperedges, _ = _semantic(detection, root)
    if semantic:
        _save_batch(semantic, root, detection, hashes, ast["nodes"] + cached_nodes)
        cached_nodes, cached_edges, cached_hyperedges, _ = _semantic(detection, root)
    extraction = {"nodes": ast["nodes"] + cached_nodes,
                  "edges": ast["edges"] + cached_edges, "hyperedges": cached_hyperedges}
    graph = build_from_json(extraction, root=root, directed=True)
    if not graph.number_of_nodes():
        raise ValueError("No graph nodes extracted. For a document corpus, submit a semantic batch first.")
    communities = cluster(graph)
    cohesion = score_all(graph, communities)
    labels = {key: f"Community {key}" for key in communities}
    output.mkdir(parents=True, exist_ok=True)
    if not to_json(graph, communities, str(output / "graph.json"), force=force):
        raise ValueError("Graph shrink refused. Confirm intended deletions, then rebuild with --force.")
    report = generate(graph, communities, cohesion, labels, god_nodes(graph),
                      surprising_connections(graph, communities), detection,
                      {"input": 0, "output": 0}, str(root))
    report += "\nDocument extraction runs in Hermes; its token usage is accounted for in the conversation.\n"
    (output / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
    _write_json(output / ".hermes-graphify.json", {
        "source_hashes": hashes, "extractor_version": version("graphifyy"),
    })
    status = _status(root, detection, output, hashes)
    status.update(nodes=graph.number_of_nodes(), edges=graph.number_of_edges())
    return status


def execute(args: argparse.Namespace) -> int:
    root = args.project.resolve()
    try:
        if args.action == "scan":
            result = scan(root)
        elif args.action == "build":
            result = build(root, args.semantic, force=args.force)
        else:
            from graphify.cli import dispatch_command

            status = scan(root)
            if status["freshness"] in {"missing", "stale"}:
                raise ValueError(f"Project graph is {status['freshness']}; run build before querying.")
            if status["pending_document_count"]:
                print(f"Document coverage is partial: {status['pending_document_count']} sources pending.",
                      file=sys.stderr)
            values = {"query": [getattr(args, "question", "")],
                      "explain": [getattr(args, "concept", "")],
                      "path": [getattr(args, "start", ""), getattr(args, "end", "")]}
            previous_argv = sys.argv
            sys.argv = ["graphify", args.action, *values[args.action], "--graph", status["graph"]]
            if args.action == "query":
                sys.argv.extend(["--budget", str(args.budget)])
            try:
                # Bypass the upstream entrypoint's assistant-skill refresh.
                dispatch_command(args.action)
            finally:
                sys.argv = previous_argv
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, ImportError) as exc:
        print(f"Graphify: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    from hermes_cli.subcommands.graphify import configure_parser

    configure_parser(parser)
    return execute(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
