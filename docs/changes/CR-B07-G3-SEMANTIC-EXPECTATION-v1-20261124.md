# B07 G3 Semantic Expectation v1

- CR ID / node / plan version: `CR-B07-G3-Semantic-Expectation-v1` / B07 semantic Gold projection / `KW-P0-2026-11-18`
- Status: `IMPLEMENTED_PENDING_INDEPENDENT_REVIEW`; approved by the project owner on 2026-11-24.
- Approval record: `D:/agent/KnowledgeWeaver/.kw/runtime/decisions/B07-G3-semantic-approval-20261124.md`.
- Approved CR SHA-256: `1bb18e5471e5989127d5b8ddcc363517cd4b103d3dd040a46cd9a1506aaac329`.
- Implementer branch: `task/b07-g3-semantic-20261124`.
- Base SHA: `8d5b92a0d3e30e01ebac12b51fdd9f5042be8528`.
- Schema version: `gold-relation-expectation/v1` (`schema_version: 1` in each expectation and sidecar manifest).

## Approved scope and 42-item queue disposition

The queue contains 42 requests in four groups: 21 individual semantic decisions, 7 narrow return reviews, 11 source-backed fact/path reviews, and 3 guarded-call identity reviews. This change provides a versioned evaluation contract and extraction lanes for only source-verifiable, directed facts. A queue entry still needs its own exact source evidence, actor, endpoint, condition, and reviewed qualifiers before it can enter a frozen dataset.

The typed vocabulary now includes `CHECKS`, `MAPS_TO`, `RETURNS`, `RAISES`, `UPDATES`, `VALIDATES`, `SCHEDULES`, `REQUIRES`, `INVALIDATES`, `REPLACES`, `DISABLES`, `OFFERS`, `COMPARES_AGAINST`, `PRODUCES`, and `REPORTS`. `TRIGGERS`, an equivalence operator, a generic catch-all, and protocol exchange/response outcome operators were not added. Rows whose source or operator remains ambiguous stay unresolved; the enum additions do not authorize Data edits or imply that all 42 queue rows are closed.

Python extraction now emits explicit return and raise outcomes, literal dictionary mapping entries, non-initialization instance state assignments, and statically resolved calls with AST branch guards. It does not infer fallthrough returns, and a bare re-raise is rejected as unresolved. Source-code execution is never used.

Normative HTML/TXT under a `specifications` path uses a separate `NORMATIVE_SPECIFICATION` provenance lane. It requires exact visible-text Evidence for modality and any condition, guard, or outcome branch; unknown normative qualifier keys are rejected. Normal extraction receives no Gold labels or injected relations. Normative duties with a reviewed modality use `NORMATIVE_OBLIGATION`, not a runtime state-change outcome.

## Versioned data contract

The canonical runtime schema is `GoldRelationExpectationManifest.model_json_schema()` in `src/knowledge_weaver/evaluation/contracts.py`. The frozen dataset manifest may bind a sidecar with `relation_expectations_file` and `relation_expectations_sha256`. The file path is normalized relative POSIX syntax within the dataset root; its digest, dataset ID/version, and path-referenced relation IDs are checked during loading.

```json
{
  "schema_version": 1,
  "dataset_id": "dataset-name",
  "dataset_version": "2026-11-24.1",
  "expectations": [
    {
      "schema_version": 1,
      "relation_id": "00000000-0000-4000-8000-000000000001",
      "predicate": "RETURNS",
      "polarity": "positive",
      "subject": {
        "source_uri": "repo://project",
        "source_version_id": "00000000-0000-4000-8000-000000000002",
        "raw_file_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "relative_path": "src/project/api.py",
        "qualified_symbol": "Config.get_loop_factory",
        "entity_kind": "function"
      },
      "object": {
        "source_uri": "repo://project",
        "source_version_id": "00000000-0000-4000-8000-000000000002",
        "raw_file_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "relative_path": "src/project/api.py",
        "qualified_symbol": "None",
        "entity_kind": "concept"
      },
      "qualifier_review_state": "REVIEWED_VALUES",
      "qualifier_values": {
        "guard": [{"condition": "loop_factory is None", "branch": "true"}]
      },
      "result_role": "return_value",
      "outcome_kind": "RETURN_VALUE",
      "evidence_basis": "SOURCE_CODE"
    }
  ]
}
```

`UNREVIEWED` accepts no qualifier values and never maps. `REVIEWED_EMPTY` asserts the runtime qualifier object is exactly empty. `REVIEWED_VALUES` requires a non-empty exact JSON object. Endpoint matching binds the exact Source URI, a SourceVersion in the active Generation, its file path/hash/Document, entity canonical symbol and kind, and the Generation-bound Mention/Evidence/Provenance chain. The mapper verifies Generation member hashes for Entity, Mention, Provenance, Document, Chunk, Evidence, Relation, and SourceVersion when expectations are present.

Direct relation IDs and inferred candidates use the same semantic projection: predicate, polarity, ordered endpoints, exact qualifier set, result role/outcome, evidence basis, source/version binding, Generation closure, and Evidence intersection for every reviewed quote. A contradictory direct ID fails closed and is not retried as an inferred match. Without an expectation sidecar, existing mapping behavior is retained. A blocked-input manifest cannot carry semantic expectations.

## Exact implementation allowlist

- `src/knowledge_weaver/evaluation/contracts.py`
- `src/knowledge_weaver/evaluation/dataset.py`
- `src/knowledge_weaver/evaluation/gold_mapping.py`
- `src/knowledge_weaver/evaluation/__init__.py`
- `src/knowledge_weaver/models/contracts.py`
- `src/knowledge_weaver/extraction/ast_extractor.py`
- `src/knowledge_weaver/extraction/text_extractor.py`
- `src/knowledge_weaver/extraction/extractor.py`
- `tests/b02/test_extraction.py`
- `tests/b07/test_dataset.py`
- `tests/b07/test_final_mapping.py`
- `docs/changes/CR-B07-G3-SEMANTIC-EXPECTATION-v1-20261124.md`

## Verification record

Environment: shared `D:/agent/KnowledgeWeaver/.venv`, CPython `3.11.18`, with `PYTHONPATH` set to this worktree's `src`. No dependency installation, paid provider call, database write, Generation mutation, or Data project change was made.

The unmodified Windows asyncio Proactor loop stalled while creating its local wake-up socket before the first async test. The targeted suite was therefore run in a test-process-only Windows Selector loop with the empty-selector wait patched to use timers; no repository or runtime code was changed for this workaround.

- Pytest command: `$env:PYTHONPATH='D:\codex\d818\KnowledgeWeaver\src'; python.exe -c "<test-only Windows Selector workaround>" tests/b02/test_extraction.py tests/b07/test_dataset.py tests/b07/test_final_mapping.py -q --tb=short`.
  - Exit code: `0`; output: `73 passed in 1.29s`.
  - Captured log SHA-256: `3039418F645B28231E34E6C6C5A9C22011779715C6977DE652928E9B4BAADD11`.
- Ruff command: shared Ruff `check` over the exact implementation/test allowlist below.
  - Exit code: `0`; output: `All checks passed!`
  - Captured log SHA-256: `A4443AFDCFB6D7363ADB285762515CCF7CF50473B1A05C20C1A50F6BED4D26B0`.
- Mypy, seven source modules with normal import following: `evaluation/__init__.py`, `evaluation/contracts.py`, `evaluation/dataset.py`, `extraction/ast_extractor.py`, `extraction/extractor.py`, `extraction/text_extractor.py`, and `models/contracts.py`.
  - Exit code: `0`; output: `Success: no issues found in 7 source files`.
  - Captured log SHA-256: `E2702F9AFC4C0F80FF1E8624F77AF7A7D2689CA184512114AAE5967A76229BA8`.
- Mypy, `evaluation/gold_mapping.py` as a strict single-file check with `--follow-imports=skip`.
  - Exit code: `0`; output: `Success: no issues found in 1 source file`.
  - Captured log SHA-256: `8E63AE22E8213E327A5E9CBD584067681681BD8EF7F25C869877887CCDCFC4FF`.
  - Limitation: the full dependency-following run for `gold_mapping.py` exceeded two minutes without a final result. An earlier full run showed three loop-variable type errors; those were fixed, and the scoped check passes. Full transitive mypy status for this module remains unverified.

## Runtime closure still required

This implementation does not claim B07 G3 acceptance. Data still needs an Orchestrator-owned versioned expectation sidecar and manifest hash for the adjudicated rows. The runtime mapper caller must supply the persisted Mention, Provenance, Source, and SourceVersion rows from an isolated Generation whose member hashes match; regenerate that Generation under the new extractor fingerprint and obtain independent review. G1–G7, runtime Evidence closure, batch allowlists, and formal acceptance gates remain in force. No integration branch, runtime ledger, Data project, prior Generation, or paid experiment was changed.
