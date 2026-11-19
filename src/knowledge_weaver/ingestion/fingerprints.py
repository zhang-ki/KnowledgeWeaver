from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys

from knowledge_weaver.models import SourceKind

PARSER_IMPLEMENTATION_VERSION = "b01-structural-parser-v1"


def parser_fingerprint(source_kind: SourceKind) -> str:
    dependency = {
        SourceKind.python_repo: "python",
        SourceKind.pdf: "docling",
        SourceKind.docx: "python-docx",
        SourceKind.markdown: "markdown-it-py",
    }[source_kind]
    try:
        dependency_version = importlib.metadata.version(dependency)
    except importlib.metadata.PackageNotFoundError:
        dependency_version = "missing"

    settings: dict[str, object] = {
        "implementation": PARSER_IMPLEMENTATION_VERSION,
        "kind": source_kind.value,
        "dependency": dependency,
        "dependency_version": dependency_version,
    }
    if source_kind is SourceKind.python_repo:
        settings["python_runtime"] = f"{sys.version_info.major}.{sys.version_info.minor}"
        settings["encoding"] = "utf-8-sig"
        settings["ast_type_comments"] = True
    elif source_kind is SourceKind.pdf:
        settings.update(
            {
                "ocr": False,
                "table_structure": True,
                "remote_services": False,
                "external_plugins": False,
                "page_images": False,
                "model_fingerprint": os.environ.get(
                    "KW_DOCLING_MODEL_FINGERPRINT", "unverified-local-cache"
                ),
            }
        )
    elif source_kind is SourceKind.docx:
        settings["body_order"] = "paragraph-table"
    else:
        settings["markdown_preset"] = "commonmark"
        settings["line_endings"] = "preserve"

    canonical = json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{PARSER_IMPLEMENTATION_VERSION}:{source_kind.value}:{digest}"
