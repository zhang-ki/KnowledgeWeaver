from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1
UUIDText = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
SHA256Text = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
Fingerprint = Annotated[str, StringConstraints(min_length=1, max_length=256)]
EmbeddingMode = Literal["query", "document"]


class SchemaModel(BaseModel):
    schema_version: Literal[1] = Field(default=1, frozen=True)
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceKind(str, Enum):
    python_repo = "python_repo"
    pdf = "pdf"
    docx = "docx"
    markdown = "markdown"


class BlockKind(str, Enum):
    heading = "heading"
    paragraph = "paragraph"
    table = "table"
    code = "code"


class StructureType(str, Enum):
    heading = "heading"
    paragraph = "paragraph"
    table = "table"
    code = "code"
    symbol = "symbol"
    synthetic = "synthetic"


class ProvenanceMethod(str, Enum):
    STATIC_ANALYSIS = "STATIC_ANALYSIS"
    LLM_EXTRACTED = "LLM_EXTRACTED"
    RULE_INFERRED = "RULE_INFERRED"
    PARSER_EXCERPT = "PARSER_EXCERPT"
    MANUAL = "MANUAL"


class EntityKind(str, Enum):
    concept = "concept"
    algorithm = "algorithm"
    parameter = "parameter"
    module = "module"
    class_ = "class"
    function = "function"
    document = "document"


class Polarity(str, Enum):
    positive = "positive"
    negative = "negative"


class GenerationState(str, Enum):
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


class PipelineServiceStatus(str, Enum):
    accepted = "accepted"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"

class SubQuestionStatus(str, Enum):
    open = "open"
    supported = "supported"
    conflicted = "conflicted"


class InvestigationStatus(str, Enum):
    running = "running"
    answered = "answered"
    insufficient = "insufficient"
    budget_exhausted = "budget_exhausted"
    failed = "failed"


class AnswerStatus(str, Enum):
    answered = "answered"
    insufficient = "insufficient"
    conflicted = "conflicted"


class IndexChannel(str, Enum):
    bm25 = "bm25"
    dense = "dense"
    symbol = "symbol"
    entity = "entity"


class ScoreDirection(str, Enum):
    higher = "higher"
    lower_is_better = "lower"


class GraphDirection(str, Enum):
    out = "out"
    incoming = "in"
    both = "both"


class ActionTool(str, Enum):
    bm25 = "bm25"
    dense = "dense"
    symbol = "symbol"
    entity = "entity"
    graph = "graph"
    rerank = "rerank"


class PolicyKind(str, Enum):
    fixed = "fixed"
    rule = "rule"
    llm = "llm"
    segr = "segr"


class Split(str, Enum):
    train = "train"
    dev = "dev"
    test = "test"


class BenchmarkCategory(str, Enum):
    single = "single"
    multihop = "multihop"
    cross_source = "cross_source"
    conflict = "conflict"
    code = "code"


class Language(str, Enum):
    zh = "zh"
    en = "en"


class CountKind(str, Enum):
    exact = "exact"
    estimated = "estimated"
    unavailable = "unavailable"


class StopReason(str, Enum):
    sufficient_evidence = "sufficient_evidence"
    low_expected_gain = "low_expected_gain"
    repeated_no_gain = "repeated_no_gain"
    actions_exhausted = "actions_exhausted"
    budget_exhausted = "budget_exhausted"
    deadline_exceeded = "deadline_exceeded"
    cancelled = "cancelled"


class RelationPredicate(str, Enum):
    CONTAINS = "CONTAINS"
    DEFINES = "DEFINES"
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"
    INHERITS = "INHERITS"
    DESCRIBES = "DESCRIBES"
    IMPLEMENTS = "IMPLEMENTS"
    USES = "USES"
    DEPENDS_ON = "DEPENDS_ON"
    RELATED_TO = "RELATED_TO"
    CONTRADICTS = "CONTRADICTS"


class PdfLocator(SchemaModel):
    kind: Literal["pdf"] = "pdf"
    page: int = Field(ge=1)
    block: int = Field(ge=0)
    bbox: tuple[float, float, float, float] | None = None

    @field_validator("bbox")
    @classmethod
    def finite_bbox(cls, value: tuple[float, float, float, float] | None) -> tuple[float, float, float, float] | None:
        if value is not None and any(not math.isfinite(item) for item in value):
            raise ValueError("bbox values must be finite")
        return value


class DocxLocator(SchemaModel):
    kind: Literal["docx"] = "docx"
    block: int = Field(ge=0)
    table: int | None = Field(default=None, ge=0)
    row: int | None = Field(default=None, ge=0)
    cell: int | None = Field(default=None, ge=0)


class MarkdownLocator(SchemaModel):
    kind: Literal["markdown"] = "markdown"
    path: NonEmptyText
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @model_validator(mode="after")
    def valid_lines(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("line_end must not precede line_start")
        return self


class PythonLocator(SchemaModel):
    kind: Literal["python"] = "python"
    path: NonEmptyText
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    qualname: str | None = None
    col_start: int | None = Field(default=None, ge=0)
    col_end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_lines_and_columns(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("line_end must not precede line_start")
        if self.col_start is not None and self.col_end is not None and self.col_end < self.col_start:
            raise ValueError("col_end must not precede col_start")
        return self


Locator = Annotated[PdfLocator | DocxLocator | MarkdownLocator | PythonLocator, Field(discriminator="kind")]


class TableCell(SchemaModel):
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    text: str


class Block(SchemaModel):
    id: UUIDText
    kind: BlockKind
    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    locator: Locator
    heading_path: list[str] = Field(default_factory=list)
    table_cells: list[TableCell] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.end < self.start:
            raise ValueError("end must not precede start")
        return self


class FileManifestEntry(SchemaModel):
    relative_path: NonEmptyText
    raw_hash: SHA256Text
    document_id: UUIDText


class Source(SchemaModel):
    id: UUIDText
    corpus_id: UUIDText
    kind: SourceKind
    logical_uri: NonEmptyText
    display_name: NonEmptyText
    license: str | None = None
    created_at: datetime


class SourceVersion(SchemaModel):
    id: UUIDText
    source_id: UUIDText
    manifest_hash: SHA256Text
    revision: str | None = None
    dirty: bool
    files: list[FileManifestEntry]

    @model_validator(mode="after")
    def unique_paths(self) -> Self:
        paths = [item.relative_path.casefold() for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("SourceVersion files must have unique relative_path values")
        return self


class Document(SchemaModel):
    id: UUIDText
    source_id: UUIDText
    relative_path: NonEmptyText
    raw_hash: SHA256Text
    parser_fingerprint: Fingerprint
    normalized_text: str
    blocks: list[Block] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def blocks_match_text(self) -> Self:
        for block in self.blocks:
            if block.end > len(self.normalized_text):
                raise ValueError("block range exceeds normalized_text")
            if self.normalized_text[block.start:block.end] != block.text:
                raise ValueError("block text must match its Unicode-codepoint range")
        return self


class ChunkSpan(SchemaModel):
    chunk_start: int = Field(ge=0)
    chunk_end: int = Field(ge=0)
    document_start: int = Field(ge=0)
    document_end: int = Field(ge=0)
    locator: Locator

    @model_validator(mode="after")
    def valid_ranges(self) -> Self:
        if self.chunk_end <= self.chunk_start or self.document_end <= self.document_start:
            raise ValueError("span ranges must be non-empty half-open intervals")
        if self.chunk_end - self.chunk_start != self.document_end - self.document_start:
            raise ValueError("span range lengths must match")
        return self


class Chunk(SchemaModel):
    id: UUIDText
    document_id: UUIDText
    parent_id: UUIDText | None = None
    is_leaf: bool
    text: str
    text_hash: SHA256Text
    spans: list[ChunkSpan]
    structure_type: StructureType
    qualname: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    duplicate_group: UUIDText | None = None
    chunker_fingerprint: Fingerprint

    @model_validator(mode="after")
    def hash_and_spans(self) -> Self:
        actual_hash = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if actual_hash != self.text_hash:
            raise ValueError("text_hash does not match chunk text")
        for span in self.spans:
            if span.chunk_end > len(self.text):
                raise ValueError("span range exceeds chunk text")
        return self


class Evidence(SchemaModel):
    id: UUIDText
    chunk_id: UUIDText
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: NonEmptyText
    locator: Locator
    provenance_id: UUIDText

    @model_validator(mode="after")
    def quote_length_matches(self) -> Self:
        if self.end <= self.start or len(self.quote) != self.end - self.start:
            raise ValueError("quote length must match its half-open range")
        return self

    def assert_matches_chunk(self, chunk: Chunk) -> None:
        if self.chunk_id != chunk.id or self.end > len(chunk.text):
            raise ValueError("evidence references a different or shorter chunk")
        if chunk.text[self.start:self.end] != self.quote:
            raise ValueError("quote does not match chunk text")
        if not any(
            span.chunk_start <= self.start and self.end <= span.chunk_end
            for span in chunk.spans
        ):
            raise ValueError("quote is outside every source-backed chunk span")


class Provenance(SchemaModel):
    id: UUIDText
    method: ProvenanceMethod
    producer_fingerprint: Fingerprint
    source_id: UUIDText
    document_id: UUIDText
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    premise_relation_ids: list[UUIDText] = Field(default_factory=list)
    rule_id: str | None = None

    @model_validator(mode="after")
    def inferred_requires_premises(self) -> Self:
        if self.method == ProvenanceMethod.RULE_INFERRED and (not self.premise_relation_ids or not self.rule_id):
            raise ValueError("RULE_INFERRED provenance requires premises and rule_id")
        return self


class Entity(SchemaModel):
    id: UUIDText
    corpus_id: UUIDText
    kind: EntityKind
    canonical_name: NonEmptyText
    namespace: NonEmptyText
    identity_key: NonEmptyText
    aliases: list[str] = Field(default_factory=list)


class Mention(SchemaModel):
    id: UUIDText
    entity_id: UUIDText
    evidence_id: UUIDText
    surface_form: NonEmptyText


class Relation(SchemaModel):
    id: UUIDText
    subject_id: UUIDText
    predicate: RelationPredicate
    object_id: UUIDText
    polarity: Polarity
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    provenance_id: UUIDText
    evidence_ids: list[UUIDText]

    @model_validator(mode="after")
    def evidence_required(self) -> Self:
        if not self.evidence_ids:
            raise ValueError("a relation requires at least one evidence id")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("relation evidence ids must be unique")
        return self


class Generation(SchemaModel):
    id: UUIDText
    corpus_id: UUIDText
    state: GenerationState
    source_version_ids: list[UUIDText]
    chunk_ids: list[UUIDText]
    entity_ids: list[UUIDText]
    relation_ids: list[UUIDText]
    index_manifest: dict[str, Any]
    embedding_fingerprint: Fingerprint
    published_at: datetime | None = None

    @model_validator(mode="after")
    def unique_members(self) -> Self:
        for field_name in ("source_version_ids", "chunk_ids", "entity_ids", "relation_ids"):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")
        return self

class QueryContext(SchemaModel):
    corpus_id: UUIDText
    generation_id: UUIDText
    request_id: UUIDText
    deadline_utc: datetime
    source_ids: list[UUIDText] | None = None
    include_inferred: bool = False

    @field_validator("deadline_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_utc must include a timezone")
        return value.astimezone(UTC)


class SubQuestion(SchemaModel):
    id: UUIDText
    text: NonEmptyText
    required_source_types: list[SourceKind] = Field(default_factory=list)
    status: SubQuestionStatus = SubQuestionStatus.open
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)


class Query(SchemaModel):
    id: UUIDText
    text: NonEmptyText
    context: QueryContext
    requested_sources: list[UUIDText] = Field(default_factory=list)
    intent: str | None = None
    subquestions: list[SubQuestion] = Field(default_factory=list)


class Usage(SchemaModel):
    elapsed_ms: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    count_kind: CountKind = CountKind.unavailable
    currency_cost: Decimal | None = Field(default=None, ge=Decimal(0))
    price_snapshot: dict[str, Any] | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class IndexQuery(SchemaModel):
    channel: IndexChannel
    context: QueryContext
    text: NonEmptyText
    top_k: int = Field(ge=1, le=100)
    entity_ids: list[UUIDText] = Field(default_factory=list)
    symbol: str | None = None


class IndexCandidate(SchemaModel):
    chunk_id: UUIDText
    rank: int = Field(ge=1)
    raw_score: float
    score_direction: ScoreDirection


class PathStep(SchemaModel):
    relation_id: UUIDText
    from_id: UUIDText
    to_id: UUIDText
    traversed_reverse: bool = False
    evidence_ids: list[UUIDText]

    @model_validator(mode="after")
    def evidence_nonempty(self) -> Self:
        if not self.evidence_ids:
            raise ValueError("every graph path step must cite evidence")
        return self


class Path(SchemaModel):
    id: UUIDText
    entity_ids: list[UUIDText]
    steps: list[PathStep]

    @model_validator(mode="after")
    def contiguous_path(self) -> Self:
        if len(self.entity_ids) != len(self.steps) + 1:
            raise ValueError("a path has one more entity than relation steps")
        for index, step in enumerate(self.steps):
            if step.from_id != self.entity_ids[index] or step.to_id != self.entity_ids[index + 1]:
                raise ValueError("path step endpoints must match adjacent entity ids")
        return self


class GraphOptions(SchemaModel):
    max_hops: int = Field(default=2, ge=1, le=3)
    max_edges: int = Field(default=200, ge=1, le=500)
    direction: GraphDirection = GraphDirection.both
    predicates: list[RelationPredicate] = Field(default_factory=list)
    seed_entity_ids: list[UUIDText] = Field(default_factory=list)


class GraphSnapshot(SchemaModel):
    context: QueryContext
    entities: list[Entity]
    mentions: list[Mention]
    relations: list[Relation]
    evidence: list[Evidence]
    provenance: list[Provenance]
    snapshot_hash: SHA256Text

    @model_validator(mode="after")
    def closed_references(self) -> Self:
        entity_ids = {item.id for item in self.entities}
        evidence_ids = {item.id for item in self.evidence}
        provenance_ids = {item.id for item in self.provenance}
        if (
            len(entity_ids) != len(self.entities)
            or len(evidence_ids) != len(self.evidence)
            or len(provenance_ids) != len(self.provenance)
        ):
            raise ValueError("graph snapshot ids must be unique")
        for mention in self.mentions:
            if mention.entity_id not in entity_ids or mention.evidence_id not in evidence_ids:
                raise ValueError("mention references must close over graph snapshot")
        for relation in self.relations:
            if relation.subject_id not in entity_ids or relation.object_id not in entity_ids:
                raise ValueError("relation endpoints must close over graph snapshot")
            if relation.provenance_id not in provenance_ids:
                raise ValueError("relation provenance must close over graph snapshot")
            if not set(relation.evidence_ids).issubset(evidence_ids):
                raise ValueError("relation evidence must close over graph snapshot")
        for evidence in self.evidence:
            if evidence.provenance_id not in provenance_ids:
                raise ValueError("evidence provenance must close over graph snapshot")
        return self


class CitationCheckItem(SchemaModel):
    id: UUIDText
    reason: NonEmptyText


class CitationCheck(SchemaModel):
    valid_ids: list[UUIDText]
    invalid: list[CitationCheckItem]

    @model_validator(mode="after")
    def disjoint_results(self) -> Self:
        valid = set(self.valid_ids)
        invalid = {item.id for item in self.invalid}
        if valid & invalid:
            raise ValueError("citation ids cannot be both valid and invalid")
        return self


class Budget(SchemaModel):
    max_steps: int = Field(default=6, ge=1)
    max_tool_calls: int = Field(default=12, ge=1)
    max_llm_calls: int = Field(default=8, ge=1)
    max_total_tokens: int = Field(default=12000, ge=1)
    deadline_utc: datetime
    max_graph_edges: int = Field(default=200, ge=1)
    max_currency_amount: Decimal | None = Field(default=None, gt=Decimal(0))

    @field_validator("deadline_utc")
    @classmethod
    def deadline_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_utc must include a timezone")
        return value.astimezone(UTC)


class CandidateScore(SchemaModel):
    action_id: UUIDText
    predicted_gain: float
    estimated_cost: float = Field(ge=0.0)
    utility: float
    reason: str | None = None


class Action(SchemaModel):
    id: UUIDText
    tool: ActionTool
    facet_id: UUIDText | None = None
    query: str = ""
    top_k: int = Field(default=10, ge=1, le=100)
    hops: int | None = Field(default=None, ge=1, le=3)
    rerank: bool = False
    estimated_tokens: int = Field(default=0, ge=0)
    estimated_ms: int = Field(default=0, ge=0)


class Decision(SchemaModel):
    action: Action | None = None
    stop_reason: StopReason | None = None
    candidate_scores: list[CandidateScore] = Field(default_factory=list)
    policy_fingerprint: Fingerprint

    @model_validator(mode="after")
    def exactly_action_or_stop(self) -> Self:
        if (self.action is None) == (self.stop_reason is None):
            raise ValueError("exactly one of action or stop_reason is required")
        return self


class RetrievalHit(SchemaModel):
    chunk_id: UUIDText
    evidence_ids: list[UUIDText] = Field(default_factory=list)
    score: float
    channel: str
    rank: int = Field(ge=1)
    contributions: list[dict[str, Any]] = Field(default_factory=list)
    path_ids: list[UUIDText] = Field(default_factory=list)
    rerank_score: float | None = None


class RetrievalResult(SchemaModel):
    context: QueryContext
    hits: list[RetrievalHit]
    paths: list[Path] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    partial: bool = False
    usage: Usage


class RetrievalQuery(SchemaModel):
    text: NonEmptyText
    context: QueryContext
    top_k: int = Field(ge=1, le=100)
    entity_ids: list[UUIDText] = Field(default_factory=list)
    symbol: str | None = None
    graph_options: GraphOptions | None = None


class RetrievalRecord(SchemaModel):
    id: UUIDText
    investigation_id: UUIDText
    step: int = Field(ge=1)
    action: Action
    context: QueryContext
    hits: list[RetrievalHit] = Field(default_factory=list)
    paths: list[Path] = Field(default_factory=list)
    usage: Usage
    warnings: list[str] = Field(default_factory=list)
    partial: bool = False
    started_at: datetime
    completed_at: datetime


class SupportRow(SchemaModel):
    facet_id: UUIDText
    evidence_id: UUIDText
    support: float = Field(ge=0.0, le=1.0)
    conflict: bool = False
    reason_code: str

    @field_validator("support")
    @classmethod
    def supported_level(cls, value: float) -> float:
        if value not in {0.0, 0.5, 1.0}:
            raise ValueError("support must be 0, 0.5, or 1")
        return value


class SupportMatrix(SchemaModel):
    rows: list[SupportRow]
    usage: Usage


class InvestigationState(SchemaModel):
    query: Query
    subquestions: list[SubQuestion]
    evidence_ledger: list[UUIDText]
    unresolved: list[str]
    conflicts: list[str]
    paths: list[Path]
    history: list[RetrievalRecord]
    remaining_budget: Budget
    candidates: list[Action]
    no_gain_count: int = Field(default=0, ge=0)
    policy_memory: dict[str, Any] = Field(default_factory=dict)


class VerifyRequest(SchemaModel):
    query: Query
    facets: list[SubQuestion]
    evidence: list[Evidence]
    paths: list[Path]


class Observation(SchemaModel):
    action: Action
    record: RetrievalRecord | None = None
    new_evidence_ids: list[UUIDText] = Field(default_factory=list)
    support_matrix: SupportMatrix | None = None
    reward: float = Field(default=0.0, ge=-1.0, le=1.0)
    usage: Usage
    error: str | None = None

class InvestigationRequest(SchemaModel):
    query: Query
    context: QueryContext
    budget: Budget
    policy: PolicyKind
    features: dict[str, Any] = Field(default_factory=dict)
    policy_artifact: str | None = None


class Claim(SchemaModel):
    text: NonEmptyText
    evidence_ids: list[UUIDText] = Field(default_factory=list)
    relation_ids: list[UUIDText] = Field(default_factory=list)


class Answer(SchemaModel):
    id: UUIDText
    investigation_id: UUIDText
    context: QueryContext
    status: AnswerStatus
    claims: list[Claim]
    unanswered: list[str] = Field(default_factory=list)
    usage: Usage


class Investigation(SchemaModel):
    id: UUIDText
    query: Query
    status: InvestigationStatus
    budget: Budget
    evidence_ledger: list[UUIDText] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    path_ids: list[UUIDText] = Field(default_factory=list)
    action_history: list[RetrievalRecord] = Field(default_factory=list)
    candidate_actions: list[Action] = Field(default_factory=list)
    checkpoint_seq: int = Field(default=0, ge=0)


class ParseLimits(SchemaModel):
    max_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    max_python_file_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    max_files: int = Field(default=500, ge=1)
    max_chunks: int = Field(default=10000, ge=1)


class ParseRequest(SchemaModel):
    source: Source
    source_version: SourceVersion
    allowed_root: NonEmptyText
    limits: ParseLimits


class RejectedFile(SchemaModel):
    path: NonEmptyText
    code: NonEmptyText
    message: NonEmptyText


class ParsedSource(SchemaModel):
    documents: list[Document]
    rejected_files: list[RejectedFile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChunkConfig(SchemaModel):
    target_chars: int = Field(default=1800, ge=1)
    max_chars: int = Field(default=4000, ge=1)
    overlap_chars: int = Field(default=200, ge=0)
    fingerprint: Fingerprint

    @model_validator(mode="after")
    def valid_size_relationships(self) -> Self:
        if self.target_chars > self.max_chars:
            raise ValueError("target_chars must not exceed max_chars")
        if self.overlap_chars >= self.max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")
        return self


class ExtractionRequest(SchemaModel):
    document: Document
    chunks: list[Chunk]
    producer_fingerprint: Fingerprint


class ExtractionResult(SchemaModel):
    document_id: UUIDText
    chunk_ids: list[UUIDText]
    entities: list[Entity] = Field(default_factory=list)
    mentions: list[Mention] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    provenance: list[Provenance] = Field(default_factory=list)
    rejected_items: list[RejectedFile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class IngestRequest(SchemaModel):
    corpus_id: UUIDText
    source_spec: Source
    idempotency_key: NonEmptyText
    strict: bool = True


class DeleteSourceRequest(SchemaModel):
    corpus_id: UUIDText
    source_id: UUIDText
    idempotency_key: NonEmptyText


class IndexReport(SchemaModel):
    job_id: UUIDText
    generation_id: UUIDText | None = None
    state: PipelineServiceStatus
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    previous_generation_id: UUIDText | None = None
    no_change: bool = False




class RunSpec(SchemaModel):
    dataset_path: NonEmptyText
    split: Split
    systems: list[str]
    ablations: list[str]
    seeds: list[int]
    corpus_manifest: SHA256Text
    provider_profiles: list[str]
    budget: Budget
    policy_artifact: str | None = None
    output_dir: NonEmptyText


class ExperimentManifest(SchemaModel):
    run_id: UUIDText
    git_sha: str | None = None
    config_hash: SHA256Text
    dataset_hash: SHA256Text
    corpus_hash: SHA256Text
    policy_hash: SHA256Text
    provider_revisions: dict[str, str] = Field(default_factory=dict)
    hardware: dict[str, Any] = Field(default_factory=dict)
    seeds: list[int]
    started_at: datetime
    finished_at: datetime | None = None
    artifacts: list[str] = Field(default_factory=list)
    status: str
    missing_runs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_timestamps(self) -> Self:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class BenchmarkGold(SchemaModel):
    source_path: NonEmptyText
    raw_hash: SHA256Text
    locator: Locator
    quote: NonEmptyText
    grade: int = Field(ge=1, le=3)
    claim_group: NonEmptyText


class BenchmarkCase(SchemaModel):
    id: UUIDText
    split: Split
    source_family_ids: list[UUIDText]
    category: BenchmarkCategory
    language: Language
    question: NonEmptyText
    answerable: bool
    gold: list[BenchmarkGold] = Field(default_factory=list)
    gold_paths: list[list[UUIDText]] = Field(default_factory=list)
    reference_claims: list[str] = Field(default_factory=list)
    reviewer: str | None = None
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def answerability_matches_gold(self) -> Self:
        if self.answerable and not self.gold and not self.gold_paths:
            raise ValueError("answerable cases require gold evidence or paths")
        return self


class ModelTokenUsage(SchemaModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    count_kind: CountKind = CountKind.unavailable
    raw_fields: dict[str, int] = Field(default_factory=dict)


class LLMRequest(SchemaModel):
    system_instruction: str
    untrusted_context: str
    response_schema: dict[str, Any]
    max_output_tokens: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0.0)


class LLMResult(SchemaModel):
    parsed_json: dict[str, Any] | None = None
    usage: ModelTokenUsage
    provider_request_id: str | None = None
    model: NonEmptyText
    revision: NonEmptyText


class EmbeddingBatch(SchemaModel):
    vectors: list[list[float]]
    dimension: int = Field(ge=1)
    normalized: bool
    model: NonEmptyText
    revision: NonEmptyText
    fingerprint: Fingerprint
    configuration_fingerprint: Fingerprint | None = None
    usage: ModelTokenUsage

    @field_validator("vectors", mode="before")
    @classmethod
    def numeric_vectors(cls, value: Any) -> Any:
        if not isinstance(value, list):
            raise TypeError("vectors must be a list")
        for vector in value:
            if not isinstance(vector, list) or any(
                type(item) not in (int, float) for item in vector
            ):
                raise ValueError("embedding values must be numeric and not booleans")
        return value

    @model_validator(mode="after")
    def consistent_finite_vectors(self) -> Self:
        for vector in self.vectors:
            if len(vector) != self.dimension:
                raise ValueError("all embedding vectors must match dimension")
            if any(not math.isfinite(value) for value in vector):
                raise ValueError("embedding values must be finite")
            norm = math.sqrt(sum(value * value for value in vector))
            if self.normalized and abs(norm - 1.0) > 0.02:
                raise ValueError("normalized vectors must have unit L2 norm")
        return self

class RerankScore(SchemaModel):
    index: int = Field(ge=0)
    score: float


__all__ = [
    "SCHEMA_VERSION",
    "Action",
    "ActionTool",
    "Answer",
    "AnswerStatus",
    "BenchmarkCase",
    "BenchmarkCategory",
    "BenchmarkGold",
    "Block",
    "BlockKind",
    "Budget",
    "CandidateScore",
    "Chunk",
    "ChunkConfig",
    "ChunkSpan",
    "CitationCheck",
    "CitationCheckItem",
    "Claim",
    "CountKind",
    "Decision",
    "DeleteSourceRequest",
    "Document",
    "DocxLocator",
    "EmbeddingBatch",
    "EmbeddingMode",
    "Entity",
    "EntityKind",
    "Evidence",
    "ExperimentManifest",
    "ExtractionRequest",
    "ExtractionResult",
    "FileManifestEntry",
    "Fingerprint",
    "Generation",
    "GenerationState",
    "GraphDirection",
    "GraphOptions",
    "GraphSnapshot",
    "IndexCandidate",
    "IndexChannel",
    "IndexQuery",
    "IndexReport",
    "IngestRequest",
    "Investigation",
    "InvestigationRequest",
    "InvestigationState",
    "InvestigationStatus",
    "LLMRequest",
    "LLMResult",
    "Language",
    "Locator",
    "MarkdownLocator",
    "Mention",
    "ModelTokenUsage",
    "NonEmptyText",
    "Observation",
    "ParseLimits",
    "ParseRequest",
    "ParsedSource",
    "Path",
    "PathStep",
    "PdfLocator",
    "PipelineServiceStatus",
    "Polarity",
    "PolicyKind",
    "Provenance",
    "ProvenanceMethod",
    "PythonLocator",
    "Query",
    "QueryContext",
    "RejectedFile",
    "Relation",
    "RelationPredicate",
    "RerankScore",
    "RetrievalHit",
    "RetrievalQuery",
    "RetrievalRecord",
    "RetrievalResult",
    "RunSpec",
    "SHA256Text",
    "SchemaModel",
    "ScoreDirection",
    "Source",
    "SourceKind",
    "SourceVersion",
    "Split",
    "StopReason",
    "StructureType",
    "SubQuestion",
    "SubQuestionStatus",
    "SupportMatrix",
    "SupportRow",
    "TableCell",
    "UUIDText",
    "Usage",
    "VerifyRequest",
]
