# B07 G3 strict runtime gate repair v1

- Parent contract: `CR-B07-G3-Semantic-Expectation-v1`; approved SHA-256 `1bb18e5471e5989127d5b8ddcc363517cd4b103d3dd040a46cd9a1506aaac329`.
- Runtime wiring parent: `B07-G3-D17` in `CR-B07-G3-SEMANTIC-RUNTIME-WIRING-v1-20261124.md`.
- Authorization: explicit project-owner instruction on 2026-11-24 following independent preacceptance verdict `FAIL` for candidate `5a515bf9af0d3b36bdcc036ece6bb7a060cf9085`.
- Branch: `task/b07-g3-semantic-20261124`; repair base: `5a515bf9af0d3b36bdcc036ece6bb7a060cf9085`.
- Scope: fail-closed entry wiring, existing semantic requirement coverage and persisted row/payload consistency only. No predicate, DTO semantic, Data, extraction, provider, or Generation scope expansion.

## Decisions

1. `run_formal_matrix` is the strict G3 entry and no longer accepts a caller-supplied `GoldMappingReport`. It requires a hash-bound semantic sidecar, a loader-recorded dataset manifest hash, a strict schedule mode, the exact frozen required relation IDs and queue digest, and unchanged loader-verified content. Any mismatch blocks before Generation lookup or provider execution.
2. The required relation ID set is independently derived from the reviewed `gold_paths` in the frozen cases. The ordered review queue digest binds `case_id`, path and edge indexes, relation ID, direction, and reviewed evidence quote hashes. The sidecar relation ID set must exactly equal this required set; the loader rejects both missing and extra entries.
3. `LoadedDataset` retains the raw manifest SHA-256 and a canonical content fingerprint covering the loaded manifest, cases, and expectations. The expectation mapping is read-only. The schedule and execution/report artifacts carry dataset ID/version, raw manifest SHA, loaded-content SHA, raw sidecar SHA, explicit required relation IDs, required-ID SHA, queue SHA, and mapping mode.
4. Pre-G3 evaluation remains available only through the separate `run_legacy_matrix_compatibility` entry, only for datasets without a semantic sidecar. Its report is explicitly marked `LEGACY_COMPATIBILITY`; it cannot run a semantic dataset.
5. Persisted `Chunk` and `Evidence` ORM columns are compared field by field with their payloads before the projection is passed to the mapper. Chunk checks cover `id`, `document_id`, `parent_id`, `is_leaf`, `text`, and `text_hash`; Evidence checks cover `id`, `chunk_id`, `provenance_id`, `start`, `end`, and `quote`.

## Exact implementation and regression allowlist

- `src/knowledge_weaver/evaluation/dataset.py`
- `src/knowledge_weaver/evaluation/scheduling.py`
- `src/knowledge_weaver/evaluation/runner.py`
- `src/knowledge_weaver/evaluation/reporting.py`
- `src/knowledge_weaver/evaluation/persisted_projection.py`
- `tests/b07/test_dataset.py`
- `tests/b07/test_semantic_entrypoint.py`
- `tests/b07/test_final_pipeline.py`
- `docs/changes/CR-B07-G3-STRICT-RUNTIME-GATE-v1-20261124.md`
- `docs/implementation/B07.final.md`
- `docs/deliveries/B07.final.md`

Regression coverage verifies missing sidecar, missing a required relation, content/hash replacement, old or mismatched schedule identity, explicit legacy compatibility, report/schedule binding mismatch, and each duplicated Chunk/Evidence column mismatch. The persisted projection failures are exercised through the formal entry and must block before provider execution.

## Acceptance boundary

No Gold labels were added and no runtime IDs were fabricated. This repair does not claim that the current dataset is frozen, a live isolated Generation was queried, the 42-row semantic review queue is closed, or G3/G5 is accepted. Existing source-backed semantics and G1–G7 gates remain in force.

## Versioning and verification evidence

- Gold expectation sidecar schema remains `gold-relation-expectation/v1`.
- Matrix schedule and matrix execution/report schemas are version `2`, with dataset, sidecar, content, and frozen-requirement identity fields.
- Exact successful verification commands and SHA-256 log digests are recorded under the Attempt 006 section in `docs/deliveries/B07.final.md`; raw logs are in `.kw/runtime/verification/B07-G3-strict-entry-repair-20261124/`.
- The shared Windows test process required a test-only Selector event-loop workaround because local socket-pair initialization blocked during pytest setup. No shared environment or repository runtime configuration was changed.
