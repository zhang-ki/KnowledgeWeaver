# CR-B07-B06-AGENT-TRACE-PORT-v1

## Status

Requested as a separate interface change from the B07 executor continuation on 2026-11-25. This Worktree does not modify B06-owned ports or implementation.

## Current gap

The public `AgentService.investigate(InvestigationRequest) -> Answer` port returns claims and aggregate Usage. `Answer` does not contain the ordered `RetrievalHit` list, retrieved `Path` records, or per-step records needed to score the frozen retrieval and directional-path metrics. Reading B06 service internals from B07 would bypass the public port and bind B07 to private implementation details.

The port also has no frozen B07 execution-profile identity that uniquely binds `fixed_agent`, `rule`, `llm_router`, `segr`, or A01–A10 to their concrete behavior, policy weights SHA, and runtime configuration SHA. Existing feature fields are not a versioned registry contract for these suite identities.

## Requested public contract

Add an additive B06-owned method and immutable result DTO, for example:

- `AgentService.investigate_with_trace(request, execution_profile) -> AgentExecutionResult`.
- `AgentExecutionResult` contains the `Answer`, ordered retrieval records/hits and paths, per-step and aggregate provider usage, actual Generation ID/SHA, and the applied route/config/weights SHA identities.
- `execution_profile` is an immutable, allowlisted B07 identity for a frozen system or ablation. The service must reject an unknown or mismatched ID/config/weights binding before provider calls.
- The request/result contain no BenchmarkCase, Gold, human review, source-family labels, or source filter copied from Gold. B07 continues to pass only the blind query, language, verified Generation, and frozen budget.

## Required acceptance evidence

1. Contract tests prove each supported system/ablation selects its own implementation and cannot alias another profile.
2. Spy tests prove ordered hits, paths, failed-step usage, and provider usage survive retries.
3. Negative tests prove missing or mismatched config/weights hashes fail before retrieval/provider calls.
4. The existing B06 service and evaluation regressions pass; formal B07 thresholds and matrix size remain unchanged.

Until this CR is implemented and integrated by the B06 owner, the B07 registry keeps the four agent systems and all ten agent ablations explicitly blocked with `BLOCKED_B06_TRACE_PORT`.
