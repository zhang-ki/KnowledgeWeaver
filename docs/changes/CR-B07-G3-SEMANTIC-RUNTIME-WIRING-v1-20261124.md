# B07 G3 semantic runtime wiring addendum v1.1

- Parent CR: `CR-B07-G3-Semantic-Expectation-v1`.
- Parent approved CR SHA-256: `1bb18e5471e5989127d5b8ddcc363517cd4b103d3dd040a46cd9a1506aaac329`.
- Parent approval record: `D:/agent/KnowledgeWeaver/.kw/runtime/decisions/B07-G3-semantic-approval-20261124.md`.
- Branch / base: `task/b07-g3-semantic-20261124` / `8d5b92a0d3e30e01ebac12b51fdd9f5042be8528`.
- Owner authorization: follow-up instruction in the source task on 2026-11-24 to wire B07's formal entry from persisted Generation members, load the hash-bound sidecar, and add entry integration coverage.
- Semantic schema: unchanged, `gold-relation-expectation/v1`.

## Decision B07-G3-D17

The earlier implementation had a strict mapper but no production evaluation call site. When a loaded frozen dataset declares `relation_expectations_file` and `relation_expectations_sha256`, `run_formal_matrix` now derives the semantic mapping itself. It loads the requested Generation through the existing persisted store, reads that Generation's membership rows and source-backed records, validates membership and row/payload/link consistency, then passes the loaded dataset expectations and persisted `Source`, `SourceVersion`, `Document`, `Chunk`, `Evidence`, `Entity`, `Mention`, `Provenance`, and `Relation` records to the strict mapper.

The dataset loader remains responsible for verifying the expectation sidecar's byte hash, dataset ID/version, and referenced relation IDs. The formal entry requires the manifest to remain hash-bound and the loaded expectation map to be complete. It ignores a caller-supplied precomputed mapping when a semantic sidecar is declared. Projection errors and incomplete records produce a `BLOCKED_GOLD_MAPPING` result before provider execution. Datasets without an expectation sidecar keep the prior caller-supplied mapping behavior.

The persistence reader is evaluation-local and read-only. It uses `PostgresKnowledgeStore.generation()` and the existing storage ORM tables; it does not alter storage schemas or public ports. The query boundary receives only a Generation ID. Gold expectations are supplied to the mapper after the persistence read; no expectation labels or injected relations enter ordinary extraction.

## Addendum allowlist

- `src/knowledge_weaver/evaluation/persisted_projection.py` — read-only persisted Generation projection loader and closure checks.
- `src/knowledge_weaver/evaluation/runner.py` — formal-entry wiring and fail-closed behavior for hash-bound semantic sidecars.
- `tests/b07/test_semantic_entrypoint.py` — persisted-projection entry, hash-binding, stale-mapping, and incomplete-member cases.
- `docs/implementation/B07.final.md` — versioned decision and implementation record.
- `docs/deliveries/B07.final.md` — changed-file list, logs, and remaining runtime gates.
- This addendum.

The original v1 schema, mapper semantics, 42-item adjudication queue, Batch allowlists outside this addendum, and G3 acceptance gates remain unchanged. No Data asset, ledger, Generation, main/integration branch, paid experiment, dependency, or frozen public model/port is changed.

## Verification boundary

The entry integration uses synthetic, content-bound records and a read-only fake session that follows the persisted store query contract. It proves the formal runner invokes the persistence adapter and mapper in the correct order and fails closed. It is not evidence of a live PostgreSQL query, an official Gold dataset, a regenerated Generation, or B07 G3 acceptance.
