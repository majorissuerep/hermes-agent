# Document extraction

Use the current Hermes session to extract relationships from the `pending_documents`
reported by `scan`. Read a manageable batch completely with `read_file`; for
large files extract a meaningful section and leave the file pending until its
coverage is complete. For scanned PDFs, use the document/OCR workflow available
in this session. An unreadable document remains pending.

Write one batch JSON under the project's `graphify-out/` directory with
`write_file`. Copy the selected files' hashes from `scan.source_hashes` into
`source_hashes`. They bind the extraction to the version you read; a batch for
changed content is rejected rather than cached under the new content.

```json
{
  "source_hashes": {"docs/auth.md": "<hash from scan>"},
  "nodes": [
    {
      "id": "doc_auth_token_expiry",
      "label": "Token expiry",
      "file_type": "document",
      "source_file": "docs/auth.md",
      "source_location": "lines 12-18"
    },
    {
      "id": "doc_auth_refresh",
      "label": "Token refresh",
      "file_type": "document",
      "source_file": "docs/auth.md",
      "source_location": "lines 20-27"
    }
  ],
  "edges": [
    {
      "source": "doc_auth_refresh",
      "target": "doc_auth_token_expiry",
      "relation": "references",
      "confidence": "EXTRACTED",
      "confidence_score": 1.0,
      "source_file": "docs/auth.md",
      "source_location": "lines 20-27",
      "weight": 1.0
    }
  ],
  "hyperedges": []
}
```

Use stable, descriptive IDs prefixed with the document identity. Give every
node and relationship a project-relative `source_file` from the selected batch
and a line, section, or page location. A relationship's endpoints must exist
in this batch or the current cached graph. `EXTRACTED` means explicit in the
source; use `INFERRED` with a lower confidence score for a reasoned connection.
Do not invent facts to connect isolated concepts. Each selected file needs at
least one source-backed node so it has a cache entry.

Run the helper's `build --semantic` with this JSON path. A complete extraction
is cached by content and this specification; unchanged documents reuse it in
later sessions. Do not mark a partially read file complete. Keep partial notes
separate until the file has been covered, then submit its complete batch.
