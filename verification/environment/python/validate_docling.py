"""Generate and parse a tiny local text PDF with Docling; never contacts model APIs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import sys
import time
import warnings
from pathlib import Path
from typing import Any

if __package__:
    from verification.environment.python.validation_safety import redact_secrets
else:
    from validation_safety import redact_secrets

SAMPLE_TEXT = (
    "KnowledgeWeaver P1 Docling smoke test.",
    "This generated PDF contains a normal text layer; OCR is disabled.",
    "Expected marker: orchard-247.",
)


def _memory_snapshot() -> dict[str, float] | None:
    try:
        import psutil

        counters = psutil.Process().memory_info()
        mib = 1024 * 1024
        snapshot = {"working_set_mb": round(counters.rss / mib, 2)}
        peak_working_set = getattr(counters, "peak_wset", None)
        if peak_working_set is not None:
            snapshot["peak_working_set_mb"] = round(peak_working_set / mib, 2)
        pagefile = getattr(counters, "pagefile", None)
        if pagefile is not None:
            snapshot["pagefile_mb"] = round(pagefile / mib, 2)
        return snapshot
    except (ImportError, OSError, ValueError):
        return None


def make_sample_pdf(path: Path) -> str:
    """Write a single-page ASCII PDF with a searchable text layer and return SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "BT",
        "/F1 14 Tf",
        "72 760 Td",
        f"({SAMPLE_TEXT[0]}) Tj",
        "0 -24 Td",
        f"({SAMPLE_TEXT[1]}) Tj",
        "0 -24 Td",
        f"({SAMPLE_TEXT[2]}) Tj",
        "ET",
    ]
    stream = ("\n".join(lines) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n%KW P1 generated fixture\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(pdf)
    return hashlib.sha256(pdf).hexdigest()


def _package_versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for package in (
        "docling",
        "docling-core",
        "docling-parse",
        "docling-ibm-models",
        "torch",
        "pytest",
        "ruff",
    ):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = None
    return result


def _interpreter_path() -> str:
    project_root = Path(__file__).resolve().parents[3]
    try:
        return Path(sys.executable).resolve().relative_to(project_root).as_posix()
    except ValueError:
        return "<external-interpreter>"


def _model_revisions() -> dict[str, str]:
    hf_home = os.environ.get("HF_HOME")
    if not hf_home:
        return {
            "layout": "unavailable: HF_HOME was not set for this run",
            "table": "unavailable: HF_HOME was not set for this run",
        }
    hub = Path(hf_home) / "hub"
    refs = {
        "layout": hub / "models--docling-project--docling-layout-heron" / "refs" / "main",
        "table": hub / "models--docling-project--docling-models" / "refs" / "v2.3.0",
    }
    requested = {"layout": "main", "table": "v2.3.0"}
    repositories = {
        "layout": "docling-project/docling-layout-heron",
        "table": "docling-project/docling-models",
    }
    return {
        role: (
            f"{repositories[role]} requested={requested[role]} resolved="
            f"{path.read_text(encoding='utf-8').strip()}"
            if path.is_file()
            else f"{repositories[role]} requested={requested[role]} resolved=unavailable"
        )
        for role, path in refs.items()
    }


class _WarningCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(redact_secrets(record.getMessage()))


def run_probe(sample_path: Path, result_path: Path | None = None) -> dict[str, Any]:
    start_total = time.perf_counter()
    before = _memory_snapshot()
    fixture_sha256 = make_sample_pdf(sample_path)
    versions = _package_versions()
    collector = _WarningCollector()
    root_logger = logging.getLogger()
    root_logger.addHandler(collector)
    caught_warnings: list[str] = []
    result_errors: list[str] = []
    page_count = 0
    table_count = 0
    extracted = ""
    status = "unavailable"
    conversion_seconds: float | None = None
    parse_error: str | None = None
    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions()
            options.do_ocr = False
            options.do_table_structure = False
            options.enable_remote_services = False
            if options.do_ocr is not False:
                raise AssertionError("OCR must be disabled for this P1 probe")
            if options.enable_remote_services is not False:
                raise AssertionError("Remote services must be disabled for this P1 probe")
            converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
            conversion_start = time.perf_counter()
            conversion = converter.convert(sample_path)
            conversion_seconds = time.perf_counter() - conversion_start
            document = conversion.document
            extracted = document.export_to_markdown()
            page_count = len(getattr(document, "pages", {}) or {})
            table_count = len(getattr(document, "tables", []) or [])
            result_errors = [
                redact_secrets(str(item)) for item in (getattr(conversion, "errors", []) or [])
            ]
            status = str(getattr(conversion, "status", "unavailable"))
            caught_warnings = [redact_secrets(str(item.message)) for item in captured]
    except Exception as exc:  # noqa: BLE001 - produce a redacted failure record, not a raw traceback
        message = redact_secrets(str(exc))
        message = message.replace(str(Path.home()), "<USER_HOME>")
        message = message.replace(str(Path(__file__).resolve().parents[3]), "<PROJECT_ROOT>")
        parse_error = f"{type(exc).__name__}: {message[:600]}"
    finally:
        root_logger.removeHandler(collector)
    after = _memory_snapshot()
    report: dict[str, Any] = {
        "status": "pass" if page_count >= 1 and all(line in extracted for line in SAMPLE_TEXT) else "fail",
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "python_executable": _interpreter_path(),
        "packages": versions,
        "ocr_enabled": False,
        "remote_services_enabled": False,
        "table_structure_enabled": False,
        "pdf_sha256": fixture_sha256,
        "pdf_bytes": sample_path.stat().st_size,
        "page_count": page_count,
        "table_count": table_count,
        "expected_text_found": all(line in extracted for line in SAMPLE_TEXT),
        "docling_status": status,
        "warnings": collector.messages + caught_warnings + result_errors,
        "conversion_seconds": round(conversion_seconds, 3) if conversion_seconds is not None else None,
        "total_seconds": round(time.perf_counter() - start_total, 3),
        "memory_before_mb": before,
        "memory_after_mb": after,
        "peak_working_set_delta_mb": (
            round(max(0.0, after["peak_working_set_mb"] - before["working_set_mb"]), 2)
            if before is not None and after is not None
            else None
        ),
        "memory_measurement": "psutil process memory_info" if before is not None else "unavailable",
        "model_revisions": _model_revisions(),
        "parse_error": parse_error,
        "probe_limitations": [
            "Table structure extraction was disabled; table_count is not an extraction result."
        ],
        "network_scope": "local PDF parsing only; no user document or model API request",
    }
    report["expected_text_found"] = all(line in extracted for line in SAMPLE_TEXT)
    if result_path is not None:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample",
        type=Path,
        default=Path("verification/environment/python/fixtures/plain_text_sample.pdf"),
    )
    parser.add_argument(
        "--result",
        type=Path,
        default=Path("verification/environment/python/results/docling_probe.json"),
    )
    args = parser.parse_args()
    report = run_probe(args.sample, args.result)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "pass" and report["page_count"] >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
