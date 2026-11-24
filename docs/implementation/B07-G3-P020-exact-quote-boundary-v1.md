# B07 G3 P020 exact quote boundary — v1

Date: 2026-11-25

Decision version: `KW-B07-G3-P020-EXACT-QUOTE-BOUNDARY/v1`

Generation implementation commit: `a4d22c0b864396e67ba04acb4da99724416de840`

Mapper and Gold quote contract commit: `f344dacc5e087e9977c044a151467bd9830f2beb`

## Contract decision

`BenchmarkGold.quote` is source text and must retain every decoded UTF-8 character. The DTO no longer strips leading or trailing whitespace on load; it still rejects a quote containing only whitespace. The serialized field shape is unchanged, so `BenchmarkGold.schema_version` remains 1. `PreparedBenchmarkCase` continues to validate the SHA-256 over the exact quote bytes.

The strict mapping adapters remain explicit and independently versioned:

- `kw-g3-physical-line-boundary-evidence/v1` accepts a Gold interval that begins at the physical line start only when the runtime direct-AST evidence begins later on that same document/version, the suffix through the ending offset is byte-for-byte identical, and the intervening prefix consists only of ASCII spaces or tabs.
- `kw-g3-static-call-resolution-qualifier/v1` removes only the extractor metadata pair `resolution=static_symbol` from static-analysis `CALLS` qualifier comparison. All semantic qualifiers remain exact; in particular, no guard or condition is dropped.

No fuzzy matching, internal-whitespace normalization, cross-source/version identity matching, or function-context substitution is permitted. The mapper report dictionary schema is version 2; the expectation sidecar schema remains version 1.

## P020-006 Dev edge 0 observation

The v1.0.15 sidecar binds relation `4c3ae496-e0fb-52c3-95c6-fa81bf0dd180`, source version key `72caa006-f375-42f3-835c-aa434a6a1e1c@7d5ef55e42ce42221f58dc49943e92ccad1fa66a`, path `sources/repositories/pyjwt/api_jwt.py`, and raw file SHA-256 `a2af9f3593c8cf9133163cb0b5b08223281b8292d5db5665309bc23c212cac76`.

The exact Gold/Data quote has 316 characters, SHA-256 `6c630aa93d369834d6320c5b2dd2278b6e23083289d3605b07f7bb7a8e3c3e2c`, and document offsets `[14901, 15217)`. The independently extracted direct AST assignment Evidence has 308 characters, SHA-256 `99ae7d20af2e100bf9f3cebc99dc55f63c5695f8cc0054c5bf51e209a6874539`, and offsets `[14909, 15217)`. The eight-character prefix consists only of ASCII spaces. The exact ending offset, document, source file hash, endpoint identities, CALLS direction, and positive polarity match.

Against isolated Generation `8dd724d8-72fd-4ab1-9e35-7cdd057aa2d2` (Generation SHA-256 `beb20dca04bfdabda1c33c7cf970d682bc1301e159e63013fcea6fa8f24dc075`), edge 0 is `PASS_EDGE0_ONLY`. The remaining path is `UNMAPPED` at relation `266f6ad2-05d2-57a7-849e-b048a4cca32d`; this one-source diagnostic is not a full path result or a formal G3 pass.

Extraction was source-only: no Gold or sidecar was supplied to extraction, provider calls were zero, and no dense embedding or Qdrant collection was used. The base Data manifest remains `BLOCKED_INPUTS`; the v1.0.15 material remains `SUPPLEMENT_ONLY; NOT_A_FORMAL_CANDIDATE`.

The runtime SourceVersion has its own local ID and the Generation verifies the exact Data raw file SHA. Its `revision` field records the host KnowledgeWeaver worktree commit rather than the upstream PyJWT revision in the sidecar. The cross-system revision is therefore reconciled by exact path and file SHA, not by equal revision strings; that distinction must remain visible in any formal provenance review.

Two RFC locator mismatches already recorded by the Data supplement remain separate, unchanged, and were not rerun: KWG-P020-003 relation `7d5036bc-9014-52a8-bf1e-db4b822a3f95` (recorded lines 507–508, verified 499–500), and relation `9a17ecfd-afde-52b1-9403-8225aa3bab86` (recorded 520–522, verified 511–513).

## Verification

- B07 tests: 133 passed.
- Ruff on the changed implementation and B07 test files: passed.
- Full `mypy src`: 75 source files, no issues.
- Persistence read was read-only and verified all Generation membership hashes plus duplicate Chunk/Evidence/Relation columns against payloads.

The machine-readable receipt, run log, and test logs are stored under `.kw/runtime/verification/B07-G3-P020-edge0-physical-boundary-20261125-01/`.
