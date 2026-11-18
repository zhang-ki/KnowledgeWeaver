# 模块接口与公共 DTO

以下是冻结规范，尚无实现。B00 在 models/ports 中实现；P3 使用隔离原型命名空间，不占正式模块。I/O async，纯计算同步，应用使用受限线程池处理 CPU 工作；禁止核心层依赖 UI/evaluation。

```python
class Parser(Protocol):
    async def parse(self, request: ParseRequest) -> ParsedSource: ...
class Chunker(Protocol):
    def split(self, document: Document, config: ChunkConfig) -> list[Chunk]: ...
class Extractor(Protocol):
    async def extract(self, request: ExtractionRequest) -> ExtractionResult: ...
class PipelineService(Protocol):
    async def ingest(self, request: IngestRequest) -> IndexReport: ...
    async def delete_source(self, request: DeleteSourceRequest) -> IndexReport: ...
class KnowledgeStore(Protocol):
    async def resolve_context(self, corpus_id: str, request_id: str) -> QueryContext: ...
    async def get_chunks(self, ids: list[str], context: QueryContext) -> list[Chunk]: ...
    async def get_evidence(self, ids: list[str], context: QueryContext) -> list[Evidence]: ...
    async def get_chunk_evidence(self, ids: list[str], context: QueryContext) -> list[Evidence]: ...
    async def validate_citations(self, ids: list[str], context: QueryContext) -> CitationCheck: ...
class CandidateIndex(Protocol):
    async def search(self, query: IndexQuery) -> list[IndexCandidate]: ...
class GraphStore(Protocol):
    async def snapshot(self, context: QueryContext) -> GraphSnapshot: ...
class Retriever(Protocol):
    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult: ...
class HybridRetriever(Retriever):
    def __init__(self, channels: Mapping[str, Retriever], reranker: Reranker | None): ...
class Reranker(Protocol):
    async def rerank(self, query: str, hits: list[RetrievalHit], context: QueryContext, limit: int) -> list[RetrievalHit]: ...
class EvidenceVerifier(Protocol):
    async def evaluate(self, request: VerifyRequest) -> SupportMatrix: ...
class RetrievalPolicy(Protocol):
    def choose(self, state: InvestigationState, candidates: list[Action]) -> Decision: ...
    def observe(self, state: InvestigationState, observation: Observation) -> InvestigationState: ...
class AgentService(Protocol):
    async def investigate(self, request: InvestigationRequest) -> Answer: ...
class GenerationProvider(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResult: ...
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str], mode: Literal['query','document']) -> EmbeddingBatch: ...
class EvaluationRunner(Protocol):
    async def run(self, spec: RunSpec) -> ExperimentManifest: ...
```

## DTO 字段

| DTO | 字段/约束 |
|---|---|
| ParseRequest | source,source_version,allowed_root,limits |
| ParsedSource | documents[],rejected_files:[{path,code,message}],warnings[] |
| ChunkConfig | target_chars=1800,max_chars=4000,overlap_chars=200,fingerprint；结构优先，parent/child；超大结构显式拆分 |
| ExtractionRequest/Result | 输入 document,chunks,producer_fingerprint；输出 document_id,chunk_ids,entities,mentions,relations,evidence,provenance,rejected_items,warnings |
| IngestRequest | corpus_id,source_spec,idempotency_key,strict=true |
| DeleteSourceRequest | corpus_id,source_id,idempotency_key；逻辑删除新 generation |
| IndexReport | job_id,generation_id?,state,counts,warnings,errors,previous_generation_id?,no_change=false |
| QueryContext | corpus_id,generation_id,request_id,deadline_utc,source_ids?,include_inferred=false；null sources 表全部，[]表空 |
| IndexQuery/Candidate | 输入 channel:bm25/dense/symbol/entity,context,text,top_k,entity_ids,symbol?；输出 chunk_id,rank,raw_score,score_direction:higher/lower |
| RetrievalQuery | text,context,top_k:1..100,entity_ids[],symbol?,graph_options? |
| RetrievalHit | chunk_id,evidence_ids[],score,channel,rank,contributions:[{channel,rank,raw_score,score_direction,rrf_score}],path_ids[],rerank_score? |
| RetrievalResult | context,hits[],paths[],warnings[],partial,usage |
| GraphSnapshot | context,entities,mentions,relations,evidence,provenance,snapshot_hash；证据闭合 |
| GraphOptions/Path | max_hops≤3,max_edges≤500,direction:out/in/both,predicates,seed_entity_ids；Path={id,entity_ids,steps:[{relation_id,from_id,to_id,traversed_reverse,evidence_ids}]} |
| CitationCheck | valid_ids,invalid:[{id,reason}]；只判断引用合法性 |
| Budget | max_steps=6,max_tool_calls=12,max_llm_calls=8,max_total_tokens=12000,deadline_utc,max_graph_edges=200,max_currency_amount? |
| Usage | elapsed_ms,tool_calls,llm_calls,input_tokens,output_tokens,count_kind:exact/estimated/unavailable,currency_cost?,price_snapshot? |
| Action | id,tool,facet_id,query,top_k,hops,rerank,estimated_tokens,estimated_ms |
| Decision | action?,stop_reason?,candidate_scores[],policy_fingerprint；二者恰一 |
| InvestigationState | Query,subquestions,evidence_ledger,unresolved,conflicts,paths,history,remaining_budget,candidates,no_gain_count,policy_memory |
| Observation | action,record,new_evidence_ids,support_matrix,reward,usage,error? |
| VerifyRequest/SupportMatrix | 输入 query,facets,evidence,paths；输出 rows:[{facet_id,evidence_id,support:0/0.5/1,conflict,reason_code}],usage |
| InvestigationRequest | query,context,budget,policy:fixed/rule/llm/segr,features,policy_artifact? |
| LLMRequest/Result | system_instruction,untrusted_context,response_schema,max_output_tokens,timeout；返回 parsed_json?,usage,provider_request_id?,error? |
| EmbeddingBatch | vectors,dimension,normalized,model,revision,fingerprint,usage；顺序数量与 texts 一致 |
| RunSpec | dataset_path,split,systems,ablations,seeds,corpus_manifest,provider_profiles,budget,policy_artifact,output_dir |
| ExperimentManifest | run_id,git_sha,config/dataset/corpus/policy hashes,provider revisions,hardware,seeds,timestamps,artifacts,status,missing_runs |

B03 提供四个 CandidateIndex、KnowledgeStore、GraphStore 与 PipelineService；DenseIndex 内调用 embedding query mode 并验证指纹。B04 根据 CandidateIndex 命中和 get_chunk_evidence 输出引用；B05 使用 EntityIndex 取 seed + GraphSnapshot，直接实现 Retriever。B04 不导入 B05，可接 fake graph；B06 负责真装配。

RRF：每路 leaf chunk 去重，1-based rank，sum(1/(60+rank))等权，tie 按 chunk_id；分数仅在原通道内可比较。Graph 命中 score=max_path 1/(1+hops)，path 中所有 edge evidence 挂到对应 chunk。最终 rerank 保留贡献与路径，无法使用时明确 degraded/fail，实验中不把降级冒充完整系统。

查询 ID 缺失抛 MISSING_REFERENCE，不能静默忽略。混合 generation 拒绝。所有过滤在 top_k/扩展前；PG hydration 再校验。应用最多100候选，SEGR rerank子集≤40；provider 长输入策略 reject/truncate 必须显式、记 count/fingerprint，原始引用不可裁掉。

## 错误与安全

ErrorEnvelope={code,message,request_id,retryable,details}，details 不含 key/连接串密码。codes：INVALID_INPUT、PATH_DENIED、UNSUPPORTED_FORMAT、PARSE_FAILED、SCHEMA_INVALID、MISSING_REFERENCE、NO_READY_GENERATION、GENERATION_CONFLICT、INDEX_NOT_READY、PROVIDER_UNAVAILABLE、RATE_LIMITED、BUDGET_EXHAUSTED、DEADLINE_EXCEEDED、LIMIT_EXCEEDED、INTERNAL_ERROR。
仅瞬态网络/限流重试≤2次，总3次，退避与超时计预算；schema最多一次修复且也计预算；同 idempotency_key 不同请求摘要返回冲突。空结果是成功；基础设施错误不能伪装空结果。取消传播，不发布半完成 generation。

## B08 本地 HTTP

API 127.0.0.1:8000，UI 127.0.0.1:8501；版本 /v1。GET /healthz 存活，/readyz 返回 PG/Qdrant/provider capability（无模型可 degraded，不谎称真实回答可用）。POST /sources 注册 allowlisted 本地路径；GET /sources 列表；POST /ingestions →202 job_id；GET /jobs/{id}；DELETE /sources/{id}?corpus_id= →202；POST /retrievals →RetrievalResult；POST /investigations →202 run_id；GET /investigations/{id} →状态/Answer；GET /evidence/{id}?corpus_id=&generation_id= →片段/定位；GET /graphs →受限子图；GET /experiments →只读 manifest。
HTTP映射400输入、403路径、404缺失、409版本/幂等冲突、422schema、429限流、503不可用、504截止。PG记录后台 job，MVP单进程执行器；重启遗留running标失败，可显式幂等重试，不声称持久分布式队列。CLI kw ingest/search/investigate/evaluate/doctor 同合同，B08实现。
