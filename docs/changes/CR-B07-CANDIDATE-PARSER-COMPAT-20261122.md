# Frozen Contract Change Request

- CR ID / node / plan version: `CR-B07-CANDIDATE-PARSER-COMPAT-20261122` / `B07 candidate intake compatibility` / `KW-P0-2026-11-18`
- Status: `APPROVED` — the user authorized this minimum compatibility repair in the Orchestrator handoff on 2026-11-22. No planning version or DTO fields change.
- Implementer: the assigned B07 candidate parser compatibility Work, branch `task/B07-candidate-parser-compat-20261122`.
- Date: 2026-11-22

## Conflict and impact

The candidate intake contains nine `.txt`/HTML files treated as Markdown-kind inputs, but the frozen `SourceKind` enum has no plain-text or HTML member and its Markdown manifest allowlist previously admitted only `.md` and `.markdown`. These inputs were rejected before parsing. The frozen `MarkdownLocator` already provides a path and inclusive source line range, so the parser can support these formats without changing DTOs or inventing column precision.

The three Python parser failures arise because `PythonLocator` represents the first line's start column and the last line's end column. For a multi-line range, the last line's column may be numerically smaller. The existing validator compared the two columns unconditionally and rejected valid ranges. The reviewed B07 Gold package has no Python `col_start`/`col_end` values on its 65 Python Gold locators, so the existing Gold mapping column check is not invoked for these rows; no mapping change is required.

## Approved minimum semantics and scope

- `.txt` and `.html` are accepted only under the existing `SourceKind.markdown` input kind and use the existing `MarkdownLocator` (`kind="markdown"`, original relative path, inclusive original source line range). There is no new source kind or locator field.
- TXT is strict UTF-8 text with original decoded line endings retained in `Document.normalized_text`; the file's raw-byte SHA-256 remains the source hash.
- HTML is strict UTF-8. Deterministic extraction emits visible text blocks, decodes character references, omits head/script/style/template/noscript and explicitly hidden content, and assigns each block a source HTML line range from the parser's source position. No markup is represented as visible text. Block text remains exact within the extracted document so existing quote matching stays exact.
- `PythonLocator` continues to require nonnegative columns and ordered lines. It requires `col_end >= col_start` only for a single-line range. Multi-line columns retain their independent first-line/last-line meaning.
- No Gold evidence, exact quote checks, public DTO shape, database behavior, Generation content, or mapping strategy changes. No dependency is added.

Exact allowlist for this CR:

- `src/knowledge_weaver/ingestion/paths.py`
- `src/knowledge_weaver/ingestion/parser.py`
- `src/knowledge_weaver/chunker.py` only if a production change is required for the existing endpoint semantics (otherwise tests cover its output)
- `src/knowledge_weaver/models/contracts.py` only for the line-aware `PythonLocator` validation described above
- `tests/b01/` and required fixtures under `tests/fixtures/b01/`
- `docs/changes/CR-B07-CANDIDATE-PARSER-COMPAT-20261122.md`

## Test plan and record

Cover all nine candidate `.txt`/HTML inputs through the manifest and parser, strict UTF-8 rejection, raw hash preservation, visible HTML extraction and source line locators, the three named Python regression paths, cross-line/single-line/invalid Python column ranges, Unicode and CRLF, then run affected B01 tests, Ruff and mypy on the affected scope. Exact commands, exit codes, and log hashes belong in the Work delivery record; this CR alone does not claim a gate passed.

### Work verification log

- Run time: `2026-11-22T11:03:54Z`; Windows Python `3.11.18`, pytest `8.4.2`, Ruff `0.17.5`, mypy `1.20.2`.
- Pytest command: `$python='D:\agent\KnowledgeWeaver\.venv\Scripts\python.exe'; $env:PYTHONPATH=(Join-Path (Get-Location) 'src'); $env:PYTHONDONTWRITEBYTECODE='1'; $logRoot=Join-Path $env:TEMP 'kw-b07-candidate-final'; & $python -m pytest -p no:cacheprovider --basetemp (Join-Path $logRoot 'pytest-tmp') -q tests/b01`.
  - Exit code: `0`; output: `30 passed, 1 skipped in 1.37s`. The skipped test requires Windows symlink creation, which is unavailable in this environment.
  - Captured log SHA-256: `0FC9B23161D67B6F68C6202BEE3FF198954A1CBAB23247EB7C7F5C91B4D57425`.
- Ruff command: `$python='D:\agent\KnowledgeWeaver\.venv\Scripts\python.exe'; & $python -m ruff check src/knowledge_weaver/ingestion/paths.py src/knowledge_weaver/ingestion/parser.py src/knowledge_weaver/ingestion/chunker.py src/knowledge_weaver/models/contracts.py tests/b01/test_candidate_parser_compat.py tests/b01/test_manifest_paths.py`.
  - Exit code: `0`; output: `All checks passed!`
  - Captured log SHA-256: `A4443AFDCFB6D7363ADB285762515CCF7CF50473B1A05C20C1A50F6BED4D26B0`.
- Mypy command: `$python='D:\agent\KnowledgeWeaver\.venv\Scripts\python.exe'; $env:MYPYPATH=(Join-Path (Get-Location) 'src'); & $python -m mypy src/knowledge_weaver/ingestion/paths.py src/knowledge_weaver/ingestion/parser.py src/knowledge_weaver/ingestion/chunker.py src/knowledge_weaver/models/contracts.py`.
  - Exit code: `0`; output: `Success: no issues found in 4 source files`.
  - Captured log SHA-256: `D9A5631F7D87A741FBECC3236286D1792D22D349FF13846D71CDC9217307BB76`.
- No dependency files changed. Candidate Gold, manifest, source roots, database and Qdrant were not modified by this Work.

### Worktree handoff record

The initial Worktree had the correct detached base but lacked the assigned branch binding. The first `git switch -c task/B07-candidate-parser-compat-20261122` was denied while creating `D:/agent/KnowledgeWeaver/.git/refs/heads/task/B07-candidate-parser-compat-20261122.lock`; the subsequent plain `git switch task/B07-candidate-parser-compat-20261122` was denied while creating `D:/agent/KnowledgeWeaver/.git/worktrees/KnowledgeWeaver2/index.lock`. Neither attempt changed project files. Orchestrator then bound the Worktree through its controlled Git environment. This Work verified branch `task/B07-candidate-parser-compat-20261122`, HEAD/base `27a92019fb545608f00604b34437da12b07f2666`, and a clean worktree before editing. The candidate changes remain uncommitted for Orchestrator-controlled submission.

## Decision

- Decision: approve the exact scope and semantics above; assign this Work as the sole implementation owner.
- Approval source: direct user authorization relayed by Orchestrator on 2026-11-22.
- Plan version: remains `KW-P0-2026-11-18`; this CR does not revise the plan or freeze any candidate Gold or manifest data.
- Review / integration: Orchestrator performs independent diff, test, and integration gates. This Work does not claim acceptance or integration.
