# CR-B07-B06-AGENT-TRACE-PORT-v1

## Status

Implemented in the isolated B07 worktree on 2026-11-25; awaiting integration review. The B06 trace blocker is cleared only for the 13 exact route bindings listed below. The formal suite gate remains blocked by B04 cache-state coverage, and the frozen fixed_agent route remains unsupported by this B06 policy implementation.

## Pre-implementation gap

The public AgentService.investigate(InvestigationRequest) -> Answer port returned claims and aggregate usage only. Answer does not contain the ordered RetrievalHit list, retrieved Path records, or per-step records needed to score frozen retrieval and directional-path metrics. Reconstructing these from the final answer would be incomplete and would misstate execution order.

The port also lacked a frozen B07 profile binding for system, ablation, implementation configuration, Generation provider configuration, policy weights, and the Generation used for retrieval.

## Implemented public interface

The existing public AgentService protocol now has this additive method:

    async investigate_with_trace(
        request: InvestigationRequest,
        execution_profile: AgentExecutionProfile,
    ) -> AgentExecutionResult

AgentExecutionProfile is an immutable DTO carrying suite_id, system_id, ablation_id, route_id, route config SHA-256, runner execution config SHA-256, Generation-provider config SHA-256, optional policy weights SHA-256, and the verified Generation ID and SHA-256.

AgentExecutionResult contains the Answer, the exact profile and Generation identity, ordered provider calls, ordered retrieval steps, aggregate usage, and validated citation IDs. Each retrieval step records sequence and policy step, action ID, actual tool/source, optional facet, action-query SHA-256, QueryContext, completion or failure, the RetrievalHit and Path values returned by that action, warnings, partial-result status, measured usage, and an observed failure code. Provider-call rows retain sequence, purpose (router, verifier, answer, or agent), status, actual provider model/revision and token usage, or failure code. The service captures these while calls execute; it does not infer retrieval traces from Answer claims.

Before any retriever or generation-provider call, the B06 implementation rejects an unsupported route, mismatched system/ablation/config SHA, policy or feature set, request context or Generation ID, Generation-provider config SHA, and missing or mismatched SEGR weights SHA. The B07 adapter separately verifies the persisted Generation manifest digest and binds the suite identity, route, budget, execution config, and complete B04 cache receipt before invoking this port.

The B07 boundary accepts only BlindBenchmarkInput. It builds InvestigationRequest from the query question and language, verified Generation context, and frozen budget. It never passes a BenchmarkCase, Gold answer, human review, source-family label, or Gold-derived source filter. Runner-side scoring continues to own all Gold access.

## Compatibility path

AgentService.investigate(request) remains available with its existing Answer return type and behavior. It uses the shared investigation implementation with trace collection disabled; existing callers need no changes. No trace is synthesized from an Answer. A B07 Agent route requires an implementation of the new public trace method and fails closed if only the legacy method is available.

## Frozen route bindings

The immutable registry in src/knowledge_weaver/agent/execution_profiles.py binds each route to a unique canonical configuration SHA over the route, system, ablation, policy, and feature overrides:

- agent.rule uses the rule policy.
- agent.llm_router uses the LLM router policy.
- agent.segr uses SEGR and requires the frozen policy weights SHA.
- agent.segr.ablation.A01 uses fixed ordering with dynamic stopping disabled.
- A02 disables graph retrieval; A03 disables relation semantic verification; A04 disables expansion; A05 disables reranking; A06 disables sufficiency stopping; A07 disables dynamic and sufficiency stopping; A08 disables source features; A09 disables learned gain; A10 sets the cost coefficient to zero. Each route is bound to SEGR and requires the frozen policy weights SHA.

The frozen suite's agent.fixed identity is intentionally unsupported. It requires fixed hybrid retrieval, graph retrieval, expansion, and reranking, while the current B06 FixedPolicy orders individual retrieval tools. Mapping it to A01 or another policy would silently change the frozen system definition.

## Cache and registry gate

The suite registry removes BLOCKED_B06_TRACE_PORT only when the exact immutable AgentSuiteRouteAdapter matches the suite route and route config SHA. It does not remove BLOCKED_CACHE_CONTROL_PORT. The adapter also requires a complete matching B04 receipt before entering B06.

Current B04 control inventory:

| Component | Actual control | Remaining uncontrolled cache scope |
| --- | --- | --- |
| Graph retriever | Graph snapshot cache can be cleared and warmed for a verified QueryContext | Graph store, entity index, and KnowledgeStore caches are not verified |
| Dense retriever | No complete reset/warm control | Shared embedding-provider cache, Qdrant server cache, and KnowledgeStore cache |
| BM25 | No complete reset/warm control | OS/filesystem page cache and KnowledgeStore cache |
| Symbol and Entity retrievers | No complete reset/warm control | Index-file/OS cache and KnowledgeStore cache |
| Hybrid retriever | No complete reset/warm control; composes its channel retrievers | All channel scopes and optional shared reranker cache |
| Agent routes | No nested cache inventory/control port is exposed to B04 | Underlying retriever/store caches and shared answer-provider cache |
| Answer generation | No shared-provider cache control | Provider-side generation cache |

Only GraphRetriever's process-local snapshot cache is currently controllable. No route is described as fully cold or warm from a label alone. Incomplete or unverifiable scope fails closed, and the formal cache blocker remains.

## Focused acceptance evidence

- tests/b06/test_agent_trace.py covers exact tool order, actual per-step hits, retained failed attempts, cross-Generation rejection before answer generation, profile/config/weights/provider-config mismatch before retrieval, and distinct frozen profile identities.
- tests/b07/test_agent_trace_executor.py covers blind request construction, exact trace and usage retention, missing cache receipt, and wrong Generation binding without entering the service.
- tests/b07/test_suite_registry.py covers all 13 exact route adapters, preservation of B04 blocking, and the fixed_agent mismatch.
- Targeted B06/B07 regressions passed: 42 tests in the trace/service/registry/Dense-executor group and 35 tests in final-pipeline/Generation-binding regressions. Ruff and compileall passed; git diff --check is recorded in the delivery log.
- No formal Test suite, paid/external provider call, database operation, or dependency synchronization was performed for this change.
