# CR-B07-G3-PREDICATE-AND-SPAN-CLOSURE-20261123

- **Status**: APPROVED (Technical Lead & Orchestrator takeover authorization, 2026-11-23)
- **Baseline**: `43f2e58` (`integration`) + `60153f1` (`task/b07-g3-physical-span-20261123`)
- **Scope**: Minimal contract extension to close B07 G3 graph semantics and runtime relation mapping without predicate downgrades, `RELATED_TO` fallbacks, or forged relation IDs.

## 1. Approved Physical-Span Group Resolution (`CR-B07-G3-SAME-PHYSICAL-QUOTE-GROUPS-20261123`)

- Approves `60153f1` (`_same_physical_quote_groups` in `src/knowledge_weaver/evaluation/gold_mapping.py`).
- Multiple reviewed claim groups for the same question and exact `quote_sha256` may resolve together for graph-edge evidence closure **only** when they share the same Generation, Document ID, Source Version, raw SHA-256, exact half-open document interval, and non-empty identical runtime `Evidence` ID set.

## 2. Minimal `RelationPredicate` Extension (`models/contracts.py`, `extraction/text_extractor.py`)

To represent the 55 out-of-vocabulary Gold edges losslessly without downgrading to `RELATED_TO`:

- **Keep** all 11 frozen predicates: `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`, `INHERITS`, `DESCRIBES`, `IMPLEMENTS`, `USES`, `DEPENDS_ON`, `RELATED_TO`, `CONTRADICTS`.
- **Add** 5 minimal lossless predicates:
  - `RETURNS`: function/method/operation returns a value, object, sentinel, or result set.
  - `MAPS_TO`: key/config maps to value/factory, or expression/token is equivalent to, exchanged for, or replaced with another.
  - `TRIGGERS`: action/condition/failure triggers an event, delay, warning, exception, or notification.
  - `UPDATES`: operation mutates, updates, invalidates, or disables fields or state.
  - `VALIDATES`: operation checks cache/condition, compares against blocklist, or validates claims/credentials.
- Modality (`REQUIRED`, `RECOMMENDED`, `SHOULD`, `OPTIONAL`) and conditional guards are preserved in `Relation.qualifiers` alongside `Polarity` and `traversed_reverse`.

## 3. Runtime Relation Resolution (`evaluation/gold_mapping.py`)

- `PreparedBenchmarkCase` and `GoldPathEdge` schemas remain frozen and unchanged.
- `map_gold_to_generation` retains exact `relation_by_id.get(edge.relation_id)` lookup as primary.
- When a Data-side `edge.relation_id` is not directly in `relation_by_id` and `relation_expectations` (keyed by Data `relation_id` from `relation_paths.jsonl` / `relations.jsonl`) is supplied, `map_gold_to_generation` resolves it **only** to an actual persisted `Relation` in the target `Generation` whose:
  1. `predicate` and `polarity` match the expectation;
  2. `subject` and `object` `Entity` names/aliases match the expected endpoint entities (respecting direction);
  3. `set(relation.evidence_ids) & mapped_ids` is non-empty.
- Missing relations, mismatched predicates/polarity/endpoints, or empty evidence intersections remain `UNMAPPED` (FAIL).
