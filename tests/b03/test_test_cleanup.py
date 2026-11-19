from __future__ import annotations

import pytest

from knowledge_weaver.storage.schema import EvidenceRow, ProvenanceRow, RelationRow
from tests.b03.test_pipeline_service import _delete_provenance_dependents


class RecordingSession:
    def __init__(self) -> None:
        self.deleted_tables: list[str] = []

    async def execute(self, statement) -> None:
        self.deleted_tables.append(statement.table.name)


@pytest.mark.asyncio
async def test_test_cleanup_deletes_provenance_references_before_parent() -> None:
    session = RecordingSession()

    await _delete_provenance_dependents(session, ["synthetic-provenance-id"])

    assert session.deleted_tables == [
        RelationRow.__tablename__,
        EvidenceRow.__tablename__,
        ProvenanceRow.__tablename__,
    ]


@pytest.mark.asyncio
async def test_test_cleanup_skips_unscoped_delete_when_no_provenance_ids() -> None:
    session = RecordingSession()

    await _delete_provenance_dependents(session, [])

    assert session.deleted_tables == []
