from __future__ import annotations

from pathlib import Path

from verification.environment.python.validate_docling import run_probe


def test_plain_text_pdf_parses_with_ocr_disabled(tmp_path: Path) -> None:
    report = run_probe(
        tmp_path / "plain_text_sample.pdf",
        tmp_path / "docling_probe.json",
    )

    assert report["status"] == "pass"
    assert report["ocr_enabled"] is False
    assert report["page_count"] == 1
    assert report["expected_text_found"] is True
    assert report["pdf_sha256"]
    assert report["memory_before_mb"] is not None
    assert report["memory_after_mb"] is not None
    assert report["peak_working_set_delta_mb"] is not None
