# 数据模型、版本与存储契约

schema_version=1，Pydantic v2 extra=forbid，UTF-8 JSON，UTC 时间。ID 为 UUID 字符串，hash 为 SHA-256；定位字符偏移为 Unicode codepoint、半开[start,end)，行/页1-based、行尾包含。代码 AST byte column 必须转换为字符偏移。

固定 UUID5 namespace=256bf6b7-a9db-5eac-944e-985ef155c9fe。派生 ID 使用 kind+canonical JSON（sorted keys，无多余空白）作为 name。Corpus/Source/Generation/Investigation 首次创建 UUID4；Source 按 corpus+规范化相对 URI 唯一，Windows resolve/normcase 仅用于私有路径映射。原始字节 hash 不做换行归一化。

| 模型 | 必填字段（? 可空；[]列表；枚举写明） |
|---|---|
| Source | id,corpus_id,kind:python_repo/pdf/docx/markdown,logical_uri,display_name,license?,created_at |
| SourceVersion | id,source_id,manifest_hash,revision?,dirty,files:[{relative_path,raw_hash,document_id}] |
| Document | id,source_id,relative_path,raw_hash,parser_fingerprint,normalized_text,blocks[],warnings[] |
| Block | id,kind:heading/paragraph/table/code,text,start,end,locator,heading_path[],table_cells[] |
| Chunk | id,document_id,parent_id?,is_leaf,text,text_hash,spans[],structure_type,qualname?,heading_path[],duplicate_group?,chunker_fingerprint |
| ChunkSpan | chunk_start/end,document_start/end,locator；合成分隔符无可引用 span |
| Evidence | id,chunk_id,start,end,quote,locator,provenance_id；非空 quote 必须等于 chunk.text[start:end]且属于一个真实 span |
| Provenance | id,method:STATIC_ANALYSIS/LLM_EXTRACTED/RULE_INFERRED/PARSER_EXCERPT/MANUAL,producer_fingerprint,source_id,document_id,confidence?,premise_relation_ids[],rule_id? |
| Entity | id,corpus_id,kind:concept/algorithm/parameter/module/class/function/document,canonical_name,namespace,identity_key,aliases[] |
| Mention | id,entity_id,evidence_id,surface_form |
| Relation | id,subject_id,predicate,object_id,polarity:positive/negative,qualifiers,provenance_id,evidence_ids[] |
| Generation | id,corpus_id,state:BUILDING/READY/FAILED/RETIRED,source_version_ids[],chunk_ids[],entity_ids[],relation_ids[],index_manifest,embedding_fingerprint,published_at? |
| Query | id,text,context,requested_sources[],intent?,subquestions[] |
| SubQuestion | id,text,required_source_types[],status:open/supported/conflicted,coverage |
| RetrievalRecord | id,investigation_id,step,action,context,hits[],paths[],usage,warnings[],partial,started_at,completed_at |
| Investigation | id,query,state,budget,evidence_ledger[],unresolved[],conflicts[],path_ids[],action_history[],candidate_actions[],checkpoint_seq,status:running/answered/insufficient/budget_exhausted/failed |
| Answer | id,investigation_id,context,status,claims:[{text,evidence_ids[],relation_ids[]}],unanswered[],usage |

Document id=(source_id,relative_path,raw_hash,parser_fingerprint)；Block=(document_id,locator,text_hash)；Chunk=(document_id,structural_path,chunker_fingerprint,text_hash)；Evidence=(chunk_id,start,end,quote_hash,provenance_id)。相同原文不同位置保留独立引用。SourceVersion id=(source_id,manifest_hash)；一个 Document 可被多个 SourceVersion 复用，通过 source_version_documents 成员表关联，不用唯一 version 外键把复用文件绑错 revision。

Entity id=(corpus_id,kind,identity_key)。代码 identity=(source_id,module_path,qualname,kind)，保留大小写；文本 identity=(namespace,normalized_name,kind)，namespace 默认 source_id，只做 NFKC/空白/casefold。跨源同名不是同实体；显式人工映射或有证据的 alias alignment 才合并；相似匹配产生候选而不是事实。Mention=(entity_id,evidence_id,surface_form)。Provenance=(method,producer_fingerprint,source_id,document_id,sorted premises,rule_id)，confidence 不进入身份。

Relation=(subject,predicate,object,polarity,canonical qualifiers,provenance_id,sorted evidence_ids)。固定 predicates：CONTAINS、DEFINES、IMPORTS、CALLS、INHERITS、DESCRIBES、IMPLEMENTS、USES、DEPENDS_ON、RELATED_TO、CONTRADICTS。RULE_INFERRED 要求 premises 非空且有 rule_id，默认检索排除。confidence 可空，模型自报不得称校准概率。路径 step 保存事实边方向与 traversed_reverse，不反向编造谓词。

locator：PDF={kind:pdf,page,block,bbox?}；DOCX={kind:docx,block,table?,row?,cell?}（除页/行外索引0-based）；Markdown={kind:markdown,path,line_start,line_end}；Python={kind:python,path,line_start,line_end,qualname?,col_start?,col_end?}。DOCX 不造页码，PDF 未可靠识别章节/表格时记录 warning。parent chunk 用于上下文，不参加默认排名；所有 hit 指 leaf。

SourceVersion 的 manifest 同时包含文件 hash、Git revision/dirty 和解析配置。Generation 每 source 恰有一个 version；该 generation 的 Document/Chunk 必须属于版本成员。Evidence→Chunk→Document→SourceVersion membership→Source 可以在 QueryContext 下准确回溯原始 revision。

B03 在每个 leaf ChunkSpan 生成持久化 PARSER_EXCERPT Evidence，B02 可增加窄范围的实体/关系 Evidence。B04/B05 不创建无法 get_evidence 解析的临时 ID。实体有至少一个当前 generation Mention；关系端点/证据均闭合。source filter 对节点 mention、边和每条 evidence 全部约束；无法闭合的路径拒绝，不能用隐藏来源作中转。

PG tables：corpora,sources,source_versions,source_version_documents,documents,blocks,chunks,provenance,evidence,entities,mentions,relations,relation_evidence,generations,generation_documents/generation_chunks/generation_entities/generation_relations,query_leases,jobs,queries,investigations,retrieval_records,answers。关系外键/唯一约束由 B03 migration 实现；大 trace/原文附件存本地 artifact，PG 保存受控引用与 hash。

Qdrant point_id=uuid5(generation_id,chunk_id,embedding_fingerprint 的规范 tuple)，payload含 corpus/generation/source/chunk/指纹；向量维度、归一化、query/document模式、model revision 属于 embedding 指纹。BM25 tokenizer 也有版本，持久化只用安全 JSON/NumPy格式。

BenchmarkCase={id,split:train/dev/test,source_family_ids[],category:single/multihop/cross_source/conflict/code,language:zh/en,question,answerable,gold:[{source_path,raw_hash,locator,quote,grade:1..3,claim_group}],gold_paths[],reference_claims[],reviewer,reviewed_at}。无答案题 gold 可空；原文 locator Gold 先于正式 parser，最终映射 ID 失败不静默删题。
