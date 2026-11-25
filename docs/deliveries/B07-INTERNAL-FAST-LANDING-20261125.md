# B07 Internal Fast Landing — 2026-11-25

## Result and boundary

The internal synthetic acceptance passed: one evidence-backed answer had a PostgreSQL-validated citation from the same persisted Generation, and one unsupported question received an explicit refusal with no claims or citations. The independent single-hop graph report also passed. This is internal Dev evidence; it is not a formal B07 result, frozen-dataset score, multi-hop closure, or `B07.final` status update. Ledger and integration changes remain Orchestrator-owned.

Final source commit: `7730c3776dcc89c376ed2bfb1af8d256edd119ca` (`fix: clarify B07 execution types`), on `task/b07-internal-fast-landing`. The delivery record is a separate docs-only commit.

## Merge ancestry and preserved behavior

- Required baseline `1052ddcc75c09acf1199889df152bceaffab1915` is an ancestor of the final candidate.
- Internal branch base: `a3700b97a4a9c799c61234e0bfb16da8804cef3c`, still the head of `task/b07-blind-formal-executor`.
- Fixed source candidate: `155c19f069d452a1b00b0d8316fab3fb1f979884`, parent `81fe185b11ed4a6f15531ad4cdb6f8d3f1345afa`.
- Merge commit `241608a507d566d80a110eebd9f08efde66a2d20` has parents `a3700b97a4a9c799c61234e0bfb16da8804cef3c` and `155c19f069d452a1b00b0d8316fab3fb1f979884`. Git auto-merged the changes; there were no textual conflict markers or manual conflict edits.
- The combined tree retains explicit empty entity/relation member hashes and source-index/extraction-profile fingerprinting in `storage/pipeline.py`; the source-index profile version remains v3. `models/contracts.py` retains the raw Gold quote boundary and rejects all-whitespace quotes. B06 ordered trace code from the B07 base remains present.
- Commit `1c568892a8e029c42fa2640d97da190036112f37` changed only the stale B03 test expectation from AST fingerprint v2 to v3; production fingerprint code was not rolled back.
- Type-only/runtime-boundary repair commit `7730c3776dcc89c376ed2bfb1af8d256edd119ca` is a separate child of `1c568892a8e029c42fa2640d97da190036112f37`.

Rollback refs were saved before the corresponding Git transactions:

- `refs/codex/rollback/b07-internal-fast-landing-20261125-001` → `a3700b97a4a9c799c61234e0bfb16da8804cef3c`
- `refs/codex/rollback/b07-internal-fingerprint-test-fix-20261125-001` → `241608a507d566d80a110eebd9f08efde66a2d20`
- `refs/codex/rollback/b07-internal-mypy-fix-20261125-001` → `1c568892a8e029c42fa2640d97da190036112f37`

Neither `main` nor `integration` was modified. The branch `task/b07-blind-formal-executor` was not moved.

## Real two-query Dense Dev run

Run artifact directory: `.kw/runtime/verification/B07-internal-twoqa-20261125-1c56889-kwpg/`. The complete sanitized result and immutable service seal are `result-twoqa.json` and `service-seal.json` in that directory. Credentials were read into process memory only and were not written to run files or logs.

Reproduction command (one-shot; the entrypoint refuses to reuse its corpus UUID or Qdrant collection):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .kw/runtime/verification/B07-internal-twoqa-20261125-1c56889-kwpg/probe_services.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .kw/runtime/verification/B07-internal-twoqa-20261125-1c56889-kwpg/probe_schema.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .kw/runtime/verification/B07-internal-twoqa-20261125-1c56889-kwpg/run.ps1
```

Exit codes: service preflight `0`, schema preflight `0`, acceptance run `0`. The service run creates a new isolated corpus and Generation in the KnowledgeWeaver PostgreSQL database and a unique Qdrant collection; it does not touch old Generations, existing collections, or production pointers.

Environment recorded in the result:

- Windows host, shared Python `3.11.18`; `uv.lock` SHA-256 `936596b08644c557a6af92be84655c55547e7b1938a33630c93523b3a36ea0ac`.
- PostgreSQL `16.16`, database `knowledgeweaver`, container `knowledgeweaver-postgres-1`, loopback port `55432`.
- Qdrant server `1.17.1`, loopback port `56333`; collection `kw_4164a915c85e44fb373238b0` contained 3 points after ingest and 3 after both queries.
- Embedding: local `qwen3-embedding-0.6b`, 1024 dimensions, loopback `56336`; document embedding usage was exact at 39 input tokens, query usage exact at 10 and 11 input tokens. Configuration fingerprint: `d997fb3df881656538176fa3882f34409092c49b9dc6c21b815baf68b8903197`.
- Generation: local `qwen3.5-4b-local`, loopback `56337`, no key or remote fallback. Exact usage was 466 input / 70 output tokens for the positive query and 467 / 66 for the refusal query.
- Environment SHA-256: `916d25b074a2c484cbbdc6e323a8842ece17e8e31f8eb1a8f18cd4f4c48df63b`.

The source was self-authored synthetic Markdown, SHA-256 `9fffb1331be1c732e0a8b1ae83f39509bbe2d2921d3f5d151af68e0180e1f8d1`. The blind query manifest and runner expectations were fixed before provider calls; their SHA-256 values are `d3b8a4ae2a6ad5a8f3b6ae0d7946fd14fd1d71268e961d62171cf7284e1ae85d` and `09534a9aba76176ffbef3be1fddc1689d320e4212bbc5866a8fd91fe67754a22`.

| Query | Real result | Runner-side check |
|---|---|---|
| “What exact synthetic token does acceptance_token return?” | `answered`: “The function `acceptance_token` returns the synthetic local token `LARKSPUR`.” Dense rank 1 score `0.82498664`; citation `4f3047d5-8bc6-5727-abb7-128aa8db4a23` resolved to PG Evidence in this Generation and matched chunk `03cbecd4-5087-5c10-bcf2-8fc66c8f3b9b`. | PASS |
| “What exact PostgreSQL hostname does acceptance_token connect to?” | `insufficient`; local model stated the retrieved passages had no hostname information. No claim and no citation. Dense returned real Top-3 hits, none supplied support for the asked hostname. | PASS |

Persisted Generation `40b9ebb5-6963-47c6-b478-c5970620643b` was reloaded after both calls with the same SHA-256 `0a502dce7e6875cbcaf4e8c70992c9f452f97186c648dd2247d1b0b2f680d66c`. SourceVersion `d60ccde8-399f-5bbe-8f20-4ac6d8ba28d1` and source file SHA were recorded. The entrypoint passes only `BlindBenchmarkInput` plus actual retrieved passages into the B07 answer-request builder; runner expectations are loaded after both generation calls.

This internal run used the real `DenseRetriever`, Qwen embedding provider, Qdrant search, PostgreSQL hydration/citation validation, and local chat provider. It did not call `PublicPortBenchmarkExecutor.execute_query`: that formal path correctly fails closed while Dense cache-scope receipts remain incomplete. The run is therefore a real internal Dev vertical slice, not proof of formal cold/warm execution or frozen B07 matrix coverage.

### Service evidence hashes

The immutable service seal SHA-256 is `114918a94c600063bec1e4568f1bf0ebfc6037e937e09a747bd3519d473e0f98`. Individual SHA-256 values:

- `run.log`: `9770c414def660a55cd39ece617b9b28c5dd9a2430d94080bc83d94f8999cdd2`
- `result-twoqa.json`: `51521166d98510d994a1e07e155604c6c718143fec37e18707897e2618617b97`
- `service-preflight-kwpg.log`: `e3c6144d1e73263bc53e4b0cd54f7a463a2ad182857181fbc61529cef54dce75`
- `schema-tables-preflight.log`: `7d41b2f5544b9fd9da8c27f5c122566a491549069541f7fac82531cd162ab7de`
- `run_dense_twoqa.py`: `ba50c75ecdef4a0439897ceaacd67b7cb644995dbf64b0fce1ef57234094c67d`
- `run.ps1`: `42296d7382e7695365a4ef53c6a56d7450d272b253b83daae6a8d393a7c4863e`

## Independent graph and regression evidence

Orchestrator independently tested the exact pre-type-fix candidate `1c568892a8e029c42fa2640d97da190036112f37`:

- Non-live regression: 434 passed, 8 skipped; log `D:/agent/KnowledgeWeaver/.kw/runtime/verification/B07-internal-1c56889-independent/pytest-not-live-model.log`, SHA-256 `a04448e30ce14a1cba2bc75d0fa3fa8b7bd7ceea6274ea90c6842e2f78c2bbe5`.
- Real PostgreSQL GraphRetriever single-hop report: `D:/agent/KnowledgeWeaver/.kw/runtime/verification/B07-internal-1c56889-independent/graph-readonly/graph_single_hop_report.json`, SHA-256 `7fb23300e62448bc487ad8952bb7c4b2cb3e02ea1c8a1e442548ac00ae1c6254`; status `PASS_INTERNAL_SINGLE_HOP_ONLY`. It verified the directed `PyJWT.decode_complete → PyJWS.decode_complete` call and directly linked Evidence, and rejected reverse direction and a wrong Generation. Source pair hashes: `api_jwt.py` `a2af9f3593c8cf9133163cb0b5b08223281b8292d5db5665309bc23c212cac76`; `api_jws.py` `80e83b3a4089f6a19692bb5b2f56784edc3bd65bf74043bad7a230f9c5bdcf12`.

## Type repair and post-fix checks

The sealed pre-fix mypy run found 12 errors across 7 source files (82 checked). The repair is limited to accurate profile literals, heterogeneous JSON payload typing, a mixed-type stable key annotation, awaitable callable typing, read-only cache fingerprint protocol properties, nullable cache failure values, and explicit runtime narrowing at Agent/Dense executor boundaries. No quality threshold, retrieval algorithm, prompt, or formal gate changed.

- `python -m mypy src`: `Success: no issues found in 82 source files`.
- Targeted B04/B06/B07 regression: 58 passed, 2 skipped in 4.45s. The two skips require real-service pytest settings; the real Dense service sample is recorded above.
- `git diff --check`: pass before commit.
- Mypy before-fix log SHA-256: `644ae9e235da0f71935e6df40a27c01d9c303336e0330cc3e1239df729d3e0f2`.
- Mypy after-fix log SHA-256: `697690db513566308936a790d2b85a99e0f286fbca95b554f9915b3cc59548a8`.
- Targeted regression log SHA-256: `68cd8109ad0322c72181d253d11a68a4a8395ca107bea9ac70c1d165908b06c1`.

The Orchestrator's 434/8 independent suite was run on `1c56889`, before the type-boundary commit. The post-fix targeted suite covers the changed B04/B06/B07 boundaries; if the integration gate requires the full non-live regression on final source SHA `7730c37`, rerun it there.

## Limits and warnings

- No formal Test, frozen Gold matrix, high-cost inference, external provider, or paid API was used.
- Formal Dense and Agent suites remain blocked by incomplete cache-state coverage; the run does not claim whole-system cold/warm control.
- The shared Python environment warned that Qdrant client `1.20.0` is outside its supported version distance from server `1.17.1`; the isolated REST operations used here succeeded. It also warned that the loopback API key was used over HTTP. No key value was printed or persisted.
- The independently reported graph and full-suite evidence is from SHA `1c56889`. The final type commit passed focused post-fix regression and mypy, but the complete independent suite was not rerun on `7730c37`.
