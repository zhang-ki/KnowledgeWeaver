# B07 G3 R2 instance-attribute resolution — CR v1

Date: 2026-11-25

Status: **IMPLEMENTED ON ISOLATED BRANCH; persisted Generation verification pending; G3 remains open**

Diagnostic base: `9390abab81e75558e1f39c271122e119b05cd370`

Branch: `task/b07-g3-semantic-20261124`

Implementation authorization: direct user instruction dated 2026-11-25, continuing from the approved R2 CR scope. This document records private extractor behavior and verification only; it does not authorize Data edits, integration, or a formal Test.

## Finding

The checksum-verified PyJWT source pair is pinned to release 2.15.1, commit `7d5ef55e42ce42221f58dc49943e92ccad1fa66a`. The v1.0.14 candidate checksum manifest contains both `api_jwt.py` and `api_jws.py`; the upstream-byte verification record covers both files. The historical `api_jwt.py` intake remains version-ambiguous because the same bytes occur in 2.15.0.

The Data source map records both files under the same Source Family and release commit but assigns different Data Source IDs and SourceVersion keys. The previous isolated KnowledgeWeaver Generation likewise persisted two SourceVersions (`142b7995-7e67-56e2-ab21-6cff3859cc14` and `133aaa77-757b-5eb7-8b1e-5279622a4a37`). The private Python index is deliberately keyed by source, version, and corpus, so the split prevents its explicit-import lookup from seeing the companion module.

An in-memory source-only diagnostic using one synthetic Source/SourceVersion identity confirmed that the index can uniquely resolve both module identities, `PyJWS`, and `PyJWS.decode_complete`. The current extractor emits the imported `PyJWS` constructor call, but it leaves `self._jws.decode_complete(...)` unresolved because `expression_target` only resolves `self` against class members. It emitted no direct `CALLS` relation from `PyJWT.decode_complete` to `PyJWS.decode_complete`.

The source also contains a visible same-type rebind: `api_jwt.py` initializes `self._jws` with `PyJWS(...)` and later assigns `_jwt_global_obj._jws = _jws_global_obj`; `api_jws.py` defines `_jws_global_obj = PyJWS()`. Any future rule must inspect and prove this rebind rather than ignore it.

The diagnostic used only the two source files. It did not read Gold expectations, write a database, or call a provider. The requested persisted Generation check did not run: two local database execution requests timed out in automatic approval review before process start. The target database was absent and not the configured production database at preflight. No Generation result is claimed.

## Private rule

When resolving a direct call shaped as `self.<attribute>.<method>(...)`, resolve the receiver only if every condition below holds within the same complete SourceVersion and corpus:

1. The caller class and its `__init__` are unique in the indexed source set.
2. A straight-line constructor assignment binds `self.<attribute>` to an instance of one uniquely resolved class.
3. The binding class is imported by explicit, unambiguous import syntax and resolves to one definition in the same indexed SourceVersion. Reexports, wildcard/dynamic imports, missing modules, duplicate definitions, alias shadowing, and cross-source/version/corpus targets remain unresolved.
4. The requested method is unique on that target class.
5. Every visible write or deletion of the attribute is checked. A direct, unconditional rebind may remain resolvable only when its receiver type is uniquely known and its value resolves to the exact same class identity. Conditional/nested writes, a different class, an unknown receiver/value, `setattr`, descriptor overrides, or dynamic attribute behavior fail closed. The PyJWT module-level singleton rebind above is the positive boundary case.
6. The call remains a direct AST assertion. The relation keeps the exact call-site Evidence and Mention; constructor/import/rebind statements justify static identity only and are not substituted as direct relation evidence.

The rule must not infer a target from names alone, enclosing function context, comments, file hashes, or Data annotations. It must not add a predicate, qualifier field, DTO, port, or persistence schema. Existing `resolution: static_symbol` remains unchanged. The extractor/profile fingerprint version must advance so a cached no-change ingest cannot retain pre-rule results.

## Implementation record

Implemented in `src/knowledge_weaver/extraction/ast_extractor.py` and fingerprinted in `src/knowledge_weaver/storage/pipeline.py` as `kw-python-source-index-v2` / `kw-python-ast-extraction-v3`. The private source index maps only a unique direct instance call to a uniquely indexed method when the explicit import, constructor binding, same-source/version scope, and visible instance/module-singleton rebindings all resolve to the same class identity. Conditional/nested writes, alias shadowing, duplicate modules or definitions, different-type rebinds, dynamic setters, decorated/inherited owner/target classes, and source/version mismatch stay unresolved. A call from a nested function does not inherit the containing class's `self` resolution.

The production relation keeps the existing `CALLS` predicate, `resolution: static_symbol` qualifier, call-site Evidence, and Mention. Import, constructor, and rebind statements are used only to establish static identity; they are not attached as direct relation evidence. No public DTO or storage shape changed.

## Exact implementation allowlist

- `src/knowledge_weaver/extraction/ast_extractor.py` — private binding analysis and fail-closed call resolution.
- `src/knowledge_weaver/storage/pipeline.py` — advance the Python extraction/index fingerprint only as required to invalidate prior cached results.
- `tests/b02/test_extraction.py` — positive and negative synthetic AST cases.
- `tests/b07/test_final_mapping.py` — only if needed to prove persisted endpoint identity and call-site Evidence survive strict mapping.
- This CR document.

Only the first three files and this CR were changed; the B07 mapper tests were run without edits.

No public DTO, Data project, existing Generation, database, production pointer, formal Test, or integration branch is in scope.

## Required synthetic cases and gates

The production B02 regressions exercise absolute and relative explicit aliases, same-type class and module-singleton rebinds, duplicate target modules/import aliases, local alias shadowing, conditional construction/rebinding, dynamic factories, different-type rebinds, cross-source/version inputs, nested-function context, and same-module construction without an explicit import. They assert that unresolved cases emit no target `CALLS`, and that the positive case retains the direct call-site Evidence/Mention and existing qualifier. After adding the explicit-import guard, the targeted extractor group passed 15 tests, including two pre-existing import-alias cases. A full-file B02 invocation was stopped after 40 seconds without producing output; no result is claimed for that invocation.

An in-memory source-only run parsed the checksum-verified `api_jwt.py` / `api_jws.py` pair together under a synthetic single Source/SourceVersion. It emitted exactly one matching edge-1 direct `CALLS`: `PyJWT.decode_complete` → `PyJWS.decode_complete`, with the exact direct call span at lines 267–273 (quote SHA-256 `612626016584dee7c9a9c347b8b279a1764e5fde2d020e1b0828d890927279a4`). The source bytes match the frozen file hashes recorded above. This is an in-memory diagnostic with synthetic IDs, not a persisted Generation, strict mapper closure, or G3 pass.

The v1.0.17 overlay checksum manifest was independently checked: 11/11 entries match and the manifest hash matches its detached hash. Its step-4 callee Evidence's physical source slice at lines 289–303 exactly matches the raw PyJWT file and the recorded quote hash. Runtime AST evidence omits four leading spaces on the first line, so its normalized quote hash differs; no evidence-closure claim is made from this in-memory run. The v1.0.17 step-4 Relation still has `qualifiers: {}`. Its audit records `json.loads` success, dict result, parse/non-dict failure paths, and subclass override scope; the empty historical qualifier is not interpreted as universal or unconditional. Runtime `RETURNS` evidence contains the method body, while its qualifiers only say `outcome_kind=RETURN_VALUE` and `result_role=return_value`; strict step-4 closure remains open.

The v1.0.17 step-3 `USES` / `PARSED_BY` and step-5 `VALIDATES` / `VALIDATED_BY` reviews preserve their reverse path traversal. Neither is replaced by `CALLS`, and no closure is claimed for those steps.

## Boundaries and unresolved items

- The v1.0.14 candidate remains `BLOCKED_INPUTS`; this CR does not authorize Data rebind or mark a formal candidate ready.
- The earlier single-file `api_jwt.py` acquisition cannot be historically labeled 2.15.1 solely from its bytes.
- The one-SourceVersion diagnostic identity is synthetic. A persisted Generation using that identity was not created due the earlier approval-review timeout; this instruction did not authorize another database attempt.
- External monkeypatching and runtime subclass behavior outside the indexed source set remain unmodeled and must not be claimed as statically proven.
- Even with the rule implemented, the full P020 path, formal Test, and G3 gate remain unclosed.

## Diagnostic records

- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/edge1_ast_diagnostic.json`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/edge1_ast_diagnostic.py`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/run_source_pair_generation.py`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_instance_method_b02_regression_20261125.log`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_instance_method_b07_mapping_20261125.log`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_instance_method_final_ruff_20261125.log`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_instance_method_final_full_mypy_20261125.log`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_instance_method_b02_regression_v2_20261125.log`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_instance_method_b07_mapping_v2_20261125.log`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_instance_method_final_ruff_v2_20261125.log`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_instance_method_full_mypy_v2_20261125.log`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_p020_edge1_source_only_memory_20261125.py`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_p020_edge1_source_only_memory_20261125.json`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_p020_edge1_source_only_memory_v2_20261125.py`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_p020_edge1_source_only_memory_v2_20261125.json`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_p020_v117_step4_comparison_v2_20261125.json`
- `.kw/runtime/verification/B07-G3-P020-edge1-source-pair-20261125-01/r2_p020_v117_raw_quote_check_20261125.json`
