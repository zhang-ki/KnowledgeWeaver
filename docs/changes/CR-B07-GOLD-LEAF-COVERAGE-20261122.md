# Frozen Contract Change Request

- CR ID / node / plan version: `CR-B07-GOLD-LEAF-COVERAGE-20261122` / `B01 chunk coverage for B07 Gold spans` / `KW-P0-2026-11-18`
- Task ID: `B01.b07_leaf_coverage`
- Status: `APPROVED` — the user authorized this bounded cross-Batch repair in the Work handoff on 2026-11-22. DTOs and frozen B07 mapping semantics remain unchanged.
- Implementer: assigned Work, branch `task/B01-b07-leaf-coverage-20261122`.
- Base / integration SHA: `456b99bc6c41d564bbb80d7b190dffa4e8d1f598`.
- Date: 2026-11-22

## Evidence and conflict

The prior exact locator audit classified 29 rows as exact quotes present only in non-leaf Python parent chunks. On the integration base, each quote had one source occurrence inside its Gold line range, matching source hashes and paths, and no exact leaf candidate. The frozen mapper correctly searches leaf chunks only, and Evidence remains leaf-only.

The formal replay must use `BenchmarkCase.model_validate` Gold quotes. `BenchmarkGold.quote` strips surrounding whitespace, so 24 of the 29 typed quotes differ from their package raw quote by 4 or 8 characters; CR and LF counts do not change. The raw package quote still matches its recorded hash and source occurrence. An earlier harness that restored raw quotes with `model_copy(update=...)` was invalid and is excluded from the final result. Historical package document IDs are also not compared with current parser IDs because the parser fingerprint changed.

The remaining nested-function case (`sources/repositories/asgiref/compatibility.py`, locator lines 28–37) was uniquely present in the parsed document and spanned three leaf intervals separated by whitespace, but no individual leaf span contained the full quote. Its typed quote was 302 characters with SHA-256 `7c5a59c7495981ca18a9579ff7c0f8ba193ea7f92d791889c0fd9c3e2dc8d955`. A common Python parent leaf that replaces those child intervals is required for the frozen mapper to map it.

## Approved semantics and scope

- Add deterministic Python leaf chunks for non-whitespace source ranges not represented by any existing leaf span.
- Coalesce only source-contiguous direct or descendant leaves under a common non-module Python parent when the intervening source is whitespace. Do not merge across sibling functions, overlap an unmerged leaf, or exceed `ChunkConfig.max_chars`; replace the coalesced leaf members while preserving their non-leaf ancestors and exact source spans.
- Keep `_span_candidates`, leaf-only Evidence behavior, DTOs, schema, IDs contract, public ports, migrations, Generation, and acceptance unchanged.
- Do not edit the Gold package, Gold quotes, source paths, hashes, splits, or manifest. No database or Qdrant writes.

Exact allowlist for this CR:

- `src/knowledge_weaver/ingestion/chunker.py`
- `tests/b01/` (focused)
- `tests/b07/test_final_mapping.py` (narrow frozen-mapper regression)
- `docs/changes/CR-B07-GOLD-LEAF-COVERAGE-20261122.md`

## Work verification record

Environment: shared Windows Python `3.11.18` at `D:\agent\KnowledgeWeaver\.venv\Scripts\python.exe`; `PYTHONPATH` pointed to this Worktree's `src`; `PYTHONDONTWRITEBYTECODE=1`. No dependency sync or environment changes.

### Focused B01/B07 regressions

Command, run from the Worktree with `$base` set to a new temporary pytest directory:

```powershell
$python='D:\agent\KnowledgeWeaver\.venv\Scripts\python.exe'
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
$env:PYTHONDONTWRITEBYTECODE='1'
& $python -m pytest -p no:cacheprovider --basetemp $base -q `
  tests/b01/test_parser_chunker.py::test_python_parent_fills_only_uncovered_source_gaps_deterministically `
  tests/b01/test_parser_chunker.py::test_python_parent_does_not_duplicate_ranges_already_covered_by_children `
  tests/b01/test_parser_chunker.py::test_python_parent_coalesces_contiguous_descendant_leaf_coverage `
  tests/b01/test_parser_chunker.py::test_python_parent_does_not_coalesce_across_sibling_functions `
  tests/b07/test_final_mapping.py::test_gold_mapping_never_uses_parent_chunks `
  tests/b07/test_final_mapping.py::test_gold_mapping_rejects_wrong_hash_path_locator_and_quote
```

- Exit code: `0`; result: `6 passed in 0.41s`.
- Captured log SHA-256: `782a03f3bc505010e7fea1c2282600cba9d1b883731abe4d7534ce0e7df1d505`.
- The tests retain parent-child relationships and assert exact source spans, deterministic output, the maximum chunk size, no duplicate/overlapping leaf intervals, no sibling-function merge, parent exclusion from B07 mapping, and rejection of invalid hash/path/locator/quote.

### Single-locator frozen-mapper replay

The audited row `21136639-eb89-5db3-9ace-1a9d80b40ed9+0` was replayed after the final chunker change using a validated `BenchmarkCase`, current parser document ID, and frozen `_make_span_mapping`. The source hash, source path, locator range `28–37`, and exact typed quote were checked.

- Result: one document occurrence, one exact leaf-span hit, `MAPPED`; exit code `0`.
- Quote length/hash: `302` / `7c5a59c7495981ca18a9579ff7c0f8ba193ea7f92d791889c0fd9c3e2dc8d955`; CR/LF counts: `0 / 9`.
- Log SHA-256: `92f4cd28d5b744cfbcc17564cd9775ca85dbe1788e88d0c2a16a7a9bfcf0e782`.

### Final 29-row offline replay

The final replay used `BenchmarkCase.model_validate` quotes, verified package source hashes, source paths, locator fields and line ranges, parsed current documents, created only in-memory source-backed Evidence from exact leaf spans, and called frozen `_make_span_mapping`. It omitted the historical document-ID equality assertion. No database or Qdrant services were invoked.

- Command: `$env:PYTHONPATH=(Join-Path (Get-Location) 'src'); $env:PYTHONDONTWRITEBYTECODE='1'; & 'D:\agent\KnowledgeWeaver\.venv\Scripts\python.exe' $scriptPath` (unique offline harness in the temporary directory).
- Exit code: `0`; result: `29/29 MAPPED`, `0 unresolved`, across `5` source files.
- Cases package SHA-256: `f4c66241b395019bb56c177c2d3d3d6d3c7151fc3572e3f6203e26ace7dc4dfb`.
- Captured replay log SHA-256: `ce62e096bc56a327e2f8d9a930e8bcce6c40e4031e45633f61d1e784f8207f4a` (independently re-hashed with the same value).

### Superseded diagnostics

Failed diagnostic artifacts were preserved with unique names. An early 28/29 run preceded the nested-coverage change (log SHA-256 `5dfe5070cc4b1effd6d7ea315a4a9c36ff80487ef8609ce45e1f1532923a24f7`). A later 21/29 run exposed that the descendant-only selection omitted leaves directly parented by the common function chunk (log SHA-256 `4bcd032d3fe480bd3767bfdc004bfc52e1d34d0701ffefb3c7d9a2dd5fb52de1`). The coalescer now includes those direct leaves, protects sibling-function boundaries, and replaces each processed group before visiting its ancestor. Its regression retains the no-overlap assertion.

## Decision and handoff

- Decision: approve the bounded B01 chunker repair and focused tests described above.
- The `29/29` replay is Work-level offline evidence only. This Work does not claim B07 acceptance, stage acceptance, or integration; Orchestrator owns independent review and integration gates.
- Plan version remains `KW-P0-2026-11-18`. No Gold or acceptance semantics changed.
