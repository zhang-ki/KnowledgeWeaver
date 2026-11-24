# B07 G3 R1/R2 prerequisite implementation — v1.0.0

Date: 2026-11-24

Implementation base: `3ed2d56f8e6f35d51e090bf25d0a870429facf3a`

Branch: `task/b07-g3-semantic-20261124`

## Decision and scope

This change implements only the minimum R1 extraction-routing observability and R2 synthetic diagnosis requested after the G3 architecture audit. It adds no relation predicate, Gold-specific rule, database schema, shared DTO, port, or Data artifact. The source tree and Generation inventory remain the only extraction inputs; Gold expectations are not read or passed to extraction.

R1 routing uses `Source.kind` as the authoritative code/text selector. Normative text is selected by the explicit `PipelineService(normative_source_ids=...)` source-profile metadata, never by path or filename. `Source.logical_uri` remains the local ingest directory. A Python source marked normative fails closed.

Each built-in `KnowledgeExtractor` result emits a compact `EXTRACTION_OBSERVATION_V1` warning. Its versioned payload records source/document IDs, source kind, lane, provider configuration, leaf-chunk count, accepted entity/mention/relation counts, rejection count, and rejection codes. Outcomes distinguish missing corpus scope, unconfigured provider, no leaf chunks, executed with candidates, and executed without candidates. The pipeline copies these observations into the `IndexReport` warnings.

The source extraction profile hash covers its schema version, source kind, normative profile, and (for text only) whether a built-in generation provider is configured. It is part of the ingest request hash and persisted under `Generation.index_manifest.extraction_profiles_by_source`; changing from an unconfigured to a configured provider therefore cannot be mistaken for a no-change ingest. Python AST routing does not depend on text-provider configuration. Existing direct `ExtractionRequest` callers without source metadata retain the `.py`/`.pyi` suffix fallback.

The observable built-in lanes are `PYTHON_AST`, `STRUCTURED_TEXT`, and `NORMATIVE_TEXT`. Injected custom `Extractor` implementations do not emit this built-in routing observation because their lane/provider semantics are not available to the pipeline.

## R2 synthetic diagnosis (no interface expansion)

The saved synthetic reproduction in `.kw/runtime/verification/B07-G3-r1-r2-prerequisites-20261124-01/r2_synthetic_reproductions.json` demonstrates:

1. Same canonical qualified name from two source IDs yields distinct entity identities.
2. A cross-module imported call and its local definition share a canonical target name but receive different entity IDs; the extractor emits no manifest-internal binding edge.
3. Two direct calls in one function receive distinct call-site evidence but both also attach the same whole-function context evidence, which can contaminate a flat evidence-based closure check.

The minimal future endpoint resolution proposal is a Generation-scoped lookup keyed by SourceVersion plus normalized source-relative module/path, qualified name, and entity kind, resolving only unique symbols. Cross-module imports should bind only when import syntax and one same-Generation definition prove the target; unresolved or ambiguous references stay unresolved. Direct assertion, guard, and enclosing context evidence need distinct roles; closure should use assertion and relevant guard evidence, while context is display/locating evidence only. The current `ExtractorPort` receives one document and its chunks, and relation evidence has no typed role. Those concrete counterexamples show that repository-scoped symbol indexing and typed relation-evidence links would require public contract/persistence changes. No such interface change is made here; it requires a separate approved CR.

## Verification and G3 boundary

R1 tests cover path-independent normative routing, provider-not-configured versus executed-empty outcomes, source-kind AST routing, and provider/profile hash invalidation. R2 uses only synthetic source code and does not read Gold, access a database, or call a paid provider. Test and static-check command lines, raw logs, and SHA-256 hashes are recorded in the attempt runtime evidence directory.

This prerequisite implementation is not G3 acceptance. The real Gold/source-backed runtime closure and independent G3 gate remain pending; no production pointer, existing database/Generation, Qdrant collection, ledger, Data project, or integration branch was changed in this attempt.
