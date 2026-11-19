from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from knowledge_weaver.ingestion.paths import (
    ArchiveSafetyError,
    PathSafetyError,
    normalize_relative_path,
    normalize_root,
    resolve_relative_file,
    validate_docx_archive,
)
from knowledge_weaver.models import SourceKind


def test_relative_paths_are_canonical_and_reject_escape() -> None:
    assert normalize_relative_path("docs\\cafe\u0301.md") == "docs/café.md"
    for value in ("../outside.md", "C:\\outside.md", "\\\\server\\share\\x.md", "a/./b.md"):
        with pytest.raises(PathSafetyError):
            normalize_relative_path(value)


def test_manifest_reports_added_changed_unchanged_and_deleted(tmp_path, source_factory) -> None:
    from knowledge_weaver.ingestion import ManifestBuilder
    from knowledge_weaver.models import SourceKind

    root = tmp_path / "docs"
    root.mkdir()
    target = root / "guide.md"
    target.write_text("first version\n", encoding="utf-8")
    source = source_factory(SourceKind.markdown)
    builder = ManifestBuilder()

    first = builder.build(source, root, revision="rev-a", dirty=False)
    assert first.source_version is not None
    assert first.changes.added == ("guide.md",)

    unchanged = builder.build(
        source,
        root,
        previous=first.source_version,
        revision="rev-a",
        dirty=False,
    )
    assert unchanged.source_version is not None
    assert unchanged.changes.unchanged == ("guide.md",)
    assert unchanged.source_version.id == first.source_version.id

    target.write_text("updated version\n", encoding="utf-8")
    (root / "new.md").write_text("new file\n", encoding="utf-8")
    changed = builder.build(
        source,
        root,
        previous=first.source_version,
        revision="rev-b",
        dirty=True,
    )
    assert changed.source_version is not None
    assert changed.changes.changed == ("guide.md",)
    assert changed.changes.added == ("new.md",)

    target.unlink()
    deleted = builder.build(
        source,
        root,
        previous=changed.source_version,
        revision="rev-c",
        dirty=True,
    )
    assert deleted.source_version is not None
    assert deleted.changes.deleted == ("guide.md",)


def test_manifest_explicitly_reports_unsupported_files(tmp_path, source_factory) -> None:
    from knowledge_weaver.ingestion import ManifestBuilder

    (tmp_path / "notes.txt").write_text("not markdown", encoding="utf-8")
    source = source_factory(SourceKind.markdown)
    result = ManifestBuilder().build(
        source, tmp_path, revision="rev-a", dirty=False
    )
    assert result.source_version is not None
    assert len(result.rejected_files) == 1
    assert result.rejected_files[0].code == "UNSUPPORTED_FORMAT"
    assert "1 files" in result.rejected_files[0].message


def test_safe_file_resolution_rejects_symlink_escape(tmp_path) -> None:
    root = tmp_path / "private"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = root / "link.md"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this Windows environment")
    with pytest.raises(PathSafetyError):
        resolve_relative_file(normalize_root(root), "link.md")


def test_docx_archive_rejects_unsafe_member_and_zip_bomb(tmp_path) -> None:
    unsafe = tmp_path / "unsafe.docx"
    with zipfile.ZipFile(unsafe, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../outside.xml", "bad")
    with pytest.raises(ArchiveSafetyError, match="unsafe member"):
        validate_docx_archive(unsafe)

    oversized = tmp_path / "oversized.docx"
    with zipfile.ZipFile(oversized, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "0" * (2 * 1024 * 1024))
    with pytest.raises(ArchiveSafetyError, match="compression-ratio"):
        validate_docx_archive(oversized)
