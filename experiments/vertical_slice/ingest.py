"""Synthetic PDF and Python ingestion with durable, locator-backed evidence."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from experiments.vertical_slice.fixture_pdf import build_pdf, parse_fixture_pdf
from experiments.vertical_slice.models import (
    SCHEMA_VERSION,
    Chunk,
    Corpus,
    Entity,
    Evidence,
    Provenance,
    Relation,
    SourceDocument,
    derived_id,
    sha256_bytes,
)

ROOT = Path(__file__).resolve().parent
CORPUS_ROOT = ROOT / "corpus"
PDF_PATH = CORPUS_ROOT / "documents" / "p3_contract.pdf"
PYTHON_PATH = CORPUS_ROOT / "python_repo" / "evidence_contract.py"
PARSER_FINGERPRINT = "p3-fixture-pdf-text-layer-v1+python-ast-v1"
CHUNKER_FINGERPRINT = "p3-line-block-leaf-v1"

ENTITY_FORMS: dict[str, tuple[str, ...]] = {
    "IngestionPipeline": ("IngestionPipeline", "ingestion pipeline"),
    "Evidence": ("Evidence", "EvidenceRecord"),
    "SourceSpan": ("SourceSpan", "source span"),
    "DemoLookupCache": ("DemoLookupCache", "demo lookup cache"),
    "PersistentStore": ("PersistentStore", "persistent store"),
    "ProcessMemory": ("ProcessMemory", "process memory"),
}


def _git_metadata() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT.parents[1],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--", "experiments/vertical_slice/corpus"],
                cwd=ROOT.parents[1],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout.strip()
        )
        return revision, dirty
    except (OSError, subprocess.SubprocessError):
        return "unavailable", True


def _entity(corpus: Corpus, name: str) -> Entity:
    canonical = next(
        (candidate for candidate, forms in ENTITY_FORMS.items() if name.casefold() in {x.casefold() for x in forms}),
        name,
    )
    identity = derived_id("entity", {"corpus_id": corpus.corpus_id, "kind": "concept", "identity_key": canonical})
    entity = corpus.entities.get(identity)
    if entity is None:
        entity = Entity(identity, canonical, ENTITY_FORMS.get(canonical, (canonical,)), canonical)
        corpus.entities[identity] = entity
    return entity


def _add_document(corpus: Corpus, kind: str, relative_path: str, raw: bytes, lines: tuple[str, ...]) -> SourceDocument:
    digest = sha256_bytes(raw)
    source_id = derived_id("source", {"corpus_id": corpus.corpus_id, "logical_uri": relative_path})
    document_id = derived_id(
        "document",
        {
            "source_id": source_id,
            "relative_path": relative_path,
            "raw_hash": digest,
            "parser_fingerprint": PARSER_FINGERPRINT,
        },
    )
    document = SourceDocument(source_id, document_id, kind, relative_path, digest, "\n".join(lines), lines)
    corpus.documents.append(document)
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        locator: dict[str, Any]
        if kind == "pdf":
            locator = {"kind": "pdf", "page": 1, "block": index}
            structure_type = "paragraph"
        else:
            locator = {
                "kind": "python",
                "path": relative_path,
                "line_start": index,
                "line_end": index,
            }
            structure_type = "code_line"
        text_hash = sha256_bytes(line.encode("utf-8"))
        chunk_id = derived_id(
            "chunk",
            {
                "document_id": document_id,
                "structural_path": [index],
                "chunker_fingerprint": CHUNKER_FINGERPRINT,
                "text_hash": text_hash,
            },
        )
        chunk = Chunk(
            chunk_id,
            document_id,
            source_id,
            kind,
            relative_path,
            line,
            locator,
            text_hash,
            structure_type,
        )
        corpus.chunks.append(chunk)
        provenance_id = derived_id(
            "provenance",
            {
                "method": "PARSER_EXCERPT",
                "producer_fingerprint": PARSER_FINGERPRINT,
                "source_id": source_id,
                "document_id": document_id,
            },
        )
        corpus.provenance.setdefault(
            provenance_id,
            Provenance(provenance_id, "PARSER_EXCERPT", PARSER_FINGERPRINT, source_id, document_id),
        )
        evidence_id = derived_id(
            "evidence",
            {"chunk_id": chunk_id, "start": 0, "end": len(line), "quote_hash": text_hash, "provenance_id": provenance_id},
        )
        corpus.evidence.append(
            Evidence(
                evidence_id,
                chunk_id,
                document_id,
                source_id,
                kind,
                relative_path,
                0,
                len(line),
                line,
                locator,
                provenance_id,
            )
        )
    return document


def _evidence_for_line(corpus: Corpus, document_id: str, line_number: int) -> Evidence:
    chunk = next(
        item
        for item in corpus.chunks
        if item.document_id == document_id and item.locator.get("line_start", item.locator.get("block")) == line_number
    )
    return next(item for item in corpus.evidence if item.chunk_id == chunk.id)


def _add_relation(
    corpus: Corpus,
    subject_name: str,
    predicate: str,
    object_name: str,
    evidence: Evidence,
    method: str,
) -> None:
    allowed = {"CONTAINS", "USES"}
    if predicate not in allowed:
        raise ValueError(f"unsupported P3 relation predicate: {predicate}")
    subject = _entity(corpus, subject_name)
    obj = _entity(corpus, object_name)
    provenance_id = derived_id(
        "provenance",
        {
            "method": method,
            "producer_fingerprint": PARSER_FINGERPRINT,
            "source_id": evidence.source_id,
            "document_id": evidence.document_id,
            "premise_relation_ids": [],
            "rule_id": "p3-curated-fixture-v1" if method == "MANUAL" else None,
        },
    )
    corpus.provenance.setdefault(
        provenance_id,
        Provenance(
            provenance_id,
            method,
            PARSER_FINGERPRINT,
            evidence.source_id,
            evidence.document_id,
            premise_relation_ids=(),
            rule_id="p3-curated-fixture-v1" if method == "MANUAL" else None,
        ),
    )
    relation_id = derived_id(
        "relation",
        {
            "subject_id": subject.id,
            "predicate": predicate,
            "object_id": obj.id,
            "polarity": "positive",
            "evidence_ids": [evidence.id],
            "provenance_id": provenance_id,
        },
    )
    corpus.relations.append(
        Relation(
            relation_id,
            subject.id,
            subject.canonical_name,
            predicate,
            obj.id,
            obj.canonical_name,
            "positive",
            (evidence.id,),
            evidence.source_kind,
            provenance_id,
            method,
        )
    )


def build_corpus(write_manifest: bool = True) -> Corpus:
    if not PDF_PATH.is_file():
        build_pdf(PDF_PATH)
    pdf_lines = tuple(parse_fixture_pdf(PDF_PATH))
    code_bytes = PYTHON_PATH.read_bytes()
    code_text = code_bytes.decode("utf-8")
    code_lines = tuple(code_text.splitlines())
    ast.parse(code_text, filename=PYTHON_PATH.name)

    revision, dirty = _git_metadata()
    manifest_path = CORPUS_ROOT / "manifest.json"
    prior_manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            prior_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prior_manifest = {}
    current_files = {
        "documents/p3_contract.pdf": sha256_bytes(PDF_PATH.read_bytes()),
        "python_repo/evidence_contract.py": sha256_bytes(code_bytes),
    }
    prior_files = {item.get("path"): item.get("raw_hash") for item in prior_manifest.get("sources", [])}
    same_source_snapshot = prior_files == current_files and prior_manifest.get("git_revision") == revision and prior_manifest.get("git_dirty") == dirty
    corpus = Corpus(
        corpus_id=prior_manifest.get("corpus_id") if same_source_snapshot else str(uuid.uuid4()),
        generation_id=prior_manifest.get("generation_id") if same_source_snapshot else str(uuid.uuid4()),
        git_revision=revision,
        git_dirty=dirty,
    )
    pdf_doc = _add_document(
        corpus,
        "pdf",
        "documents/p3_contract.pdf",
        PDF_PATH.read_bytes(),
        pdf_lines,
    )
    code_doc = _add_document(
        corpus,
        "python_repo",
        "python_repo/evidence_contract.py",
        code_bytes,
        code_lines,
    )

    # Curated text rule: only this exact synthetic sentence yields this edge.
    pipeline_line = next(i for i, line in enumerate(pdf_lines, 1) if "ingestion pipeline stores" in line)
    pipeline_evidence = _evidence_for_line(corpus, pdf_doc.document_id, pipeline_line)
    _add_relation(corpus, "IngestionPipeline", "USES", "Evidence", pipeline_evidence, "MANUAL")

    persistent_line = next(i for i, line in enumerate(pdf_lines, 1) if "PersistentStore" in line)
    persistent_evidence = _evidence_for_line(corpus, pdf_doc.document_id, persistent_line)
    _add_relation(corpus, "DemoLookupCache", "USES", "PersistentStore", persistent_evidence, "MANUAL")

    tree = ast.parse(code_text, filename=PYTHON_PATH.name)
    field_node: ast.AnnAssign | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "EvidenceRecord":
            field_node = next(
                (
                    member
                    for member in node.body
                    if isinstance(member, ast.AnnAssign)
                    and isinstance(member.target, ast.Name)
                    and member.target.id == "span"
                    and isinstance(member.annotation, ast.Name)
                    and member.annotation.id == "SourceSpan"
                ),
                None,
            )
            break
    if field_node is None:
        raise ValueError("EvidenceRecord.span: SourceSpan AST relation is missing")
    code_evidence = _evidence_for_line(corpus, code_doc.document_id, field_node.lineno)
    _add_relation(corpus, "EvidenceRecord", "CONTAINS", "SourceSpan", code_evidence, "STATIC_ANALYSIS")

    conflict_match = next(
        (i for i, line in enumerate(code_lines, 1) if re.search(r"demo lookup cache uses ProcessMemory", line, re.IGNORECASE)),
        None,
    )
    if conflict_match is None:
        raise ValueError("synthetic cache disagreement comment is missing")
    conflict_evidence = _evidence_for_line(corpus, code_doc.document_id, conflict_match)
    _add_relation(corpus, "DemoLookupCache", "USES", "ProcessMemory", conflict_evidence, "PARSER_EXCERPT")

    corpus.entity_mappings = [
        {
            "canonical_name": canonical,
            "forms": list(forms),
            "basis": "Explicit human-curated mapping for this synthetic P3 corpus.",
        }
        for canonical, forms in ENTITY_FORMS.items()
    ]
    if write_manifest:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "plan_version": "KW-P0-2026-11-18",
            "corpus_id": corpus.corpus_id,
            "generation_id": corpus.generation_id,
            "git_revision": revision,
            "git_dirty": dirty,
            "parser_fingerprint": PARSER_FINGERPRINT,
            "chunker_fingerprint": CHUNKER_FINGERPRINT,
            "synthetic_public_corpus": True,
            "sources": [
                {
                    "kind": doc.source_kind,
                    "path": doc.relative_path,
                    "raw_hash": doc.raw_hash,
                    "source_id": doc.source_id,
                    "document_id": doc.document_id,
                }
                for doc in corpus.documents
            ],
            "explicit_entity_mappings": corpus.entity_mappings,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return corpus
