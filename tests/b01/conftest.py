from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Coroutine

import pytest

from knowledge_weaver.models import ParseLimits, ParseRequest, Source, SourceKind
from knowledge_weaver.ingestion import ManifestBuilder

FIXTURES = Path(__file__).parents[1] / "fixtures" / "b01"
SOURCE_IDS = {
    SourceKind.python_repo: "00000000-0000-4000-8000-000000000101",
    SourceKind.pdf: "00000000-0000-4000-8000-000000000102",
    SourceKind.docx: "00000000-0000-4000-8000-000000000103",
    SourceKind.markdown: "00000000-0000-4000-8000-000000000104",
}
CORPUS_ID = "11111111-1111-4111-8111-111111111111"


def make_source(kind: SourceKind, display_name: str | None = None) -> Source:
    return Source(
        id=SOURCE_IDS[kind],
        corpus_id=CORPUS_ID,
        kind=kind,
        logical_uri=f"fixture://{kind.value}",
        display_name=display_name or f"Synthetic {kind.value} source",
        created_at=datetime(2026, 11, 19, tzinfo=UTC),
    )


def make_request(root: Path, kind: SourceKind, source: Source | None = None) -> ParseRequest:
    source = source or make_source(kind)
    manifest = ManifestBuilder().build(
        source,
        root,
        ParseLimits(),
        revision="synthetic-revision",
        dirty=False,
    )
    assert manifest.source_version is not None
    return ParseRequest(
        source=source,
        source_version=manifest.source_version,
        allowed_root=str(root.resolve()),
        limits=ParseLimits(),
    )


def copy_fixture(name: str, root: Path, target_name: str | None = None) -> Path:
    target = root / (target_name or name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / name, target)
    return target


@pytest.fixture
def run_immediate_async(monkeypatch):
    import knowledge_weaver.ingestion.parser as parser_module

    async def immediate_to_thread(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(parser_module.asyncio, "to_thread", immediate_to_thread)

    def run(coroutine: Coroutine[Any, Any, Any]) -> Any:
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        raise AssertionError("Parser coroutine yielded unexpectedly in the deterministic test runner.")

    return run


@pytest.fixture
def source_factory():
    return make_source


@pytest.fixture
def request_factory():
    return make_request


@pytest.fixture
def fixture_copier():
    return copy_fixture
