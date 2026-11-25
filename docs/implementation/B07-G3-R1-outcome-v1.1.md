# B07 G3 R1 outcome refinement — v1.1.0

Date: 2026-11-24

Base commit: `1799672520b61d18e1f35cd52e7f859f4b853615`

This addendum supersedes the v1 observation envelope and outcome semantics in `B07-G3-R1-R2-prerequisite-v1.md`; the source-routing behavior is otherwise unchanged. It changes no public DTO, port, database schema, predicate, or R2 interface proposal.

The built-in extraction warning now uses prefix `EXTRACTION_OBSERVATION_V2:` and payload `schema_version: 2`. The routing profile version is also advanced so a previously persisted v1 profile cannot take the no-change fast path without producing the refined observation on the next ingest.

Outcome rules are:

- `PROVIDER_FAILED` and `PARTIAL_PROVIDER_FAILURE` distinguish provider/repair timeout or error; the partial form is used when accepted entity, mention, or relation counts are nonzero.
- `STRUCTURE_REJECTED` and `PARTIAL_STRUCTURE_REJECTION` distinguish invalid structured output or AST parse failure, again retaining accepted counts in the partial form.
- `EXECUTED_WITH_RELATIONS` requires at least one accepted relation.
- `EXECUTED_NO_RELATIONS` means execution completed with zero accepted relations. Entity-only and truly empty results share this outcome, while the accepted entity/mention counts distinguish them.
- `EXECUTED_NO_RELATIONS_WITH_REJECTIONS` identifies zero accepted relations with other local rejections. Pre-execution outcomes for missing corpus scope, missing provider, and no leaf chunks remain separate.

The B02 regressions cover entity-only output, a valid empty response, provider timeout/error, schema-invalid output, and partial entity/mention counts followed by a timeout. B03's observation filter fixture tracks schema version 2. R2 is unchanged; its approved public schema work remains outside this addendum.
