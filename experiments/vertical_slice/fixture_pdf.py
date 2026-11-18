"""Build and parse the tiny ASCII text PDF used by the offline P3 corpus."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


PDF_LINES = (
    "KnowledgeWeaver P3 synthetic public fixture.",
    "The ingestion pipeline stores each source span as a durable EvidenceRecord.",
    "The demo lookup cache uses a PersistentStore and survives process restart.",
)


def _pdf_string(value: str) -> bytes:
    escaped = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"({escaped}) Tj".encode("ascii")


def build_pdf(path: Path) -> str:
    """Write a valid, one-page text-layer PDF without external packages."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # NUL is legal PDF whitespace and makes Git treat the fixture as binary.
    ops = [b"BT", b"\x00/F1 12 Tf", b"72 740 Td"]
    for index, line in enumerate(PDF_LINES):
        if index:
            ops.append(b"0 -24 Td")
        ops.append(_pdf_string(line))
    ops.append(b"ET")
    stream = b"\n".join(ops) + b"\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    # PDF readers expect a binary marker so tools do not rewrite its xref lines.
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n%KW P3 synthetic fixture\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode("ascii"))
        data.extend(body + b"\nendobj\n")
    xref_offset = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def parse_fixture_pdf(path: Path) -> list[str]:
    """Extract literal Tj strings from this generated PDF subset.

    This is intentionally not a general-purpose PDF parser. It rejects files
    outside the uncompressed, one-page fixture format instead of guessing.
    """
    data = path.read_bytes()
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data:
        raise ValueError("invalid PDF fixture")
    if b"/Count 1" not in data:
        raise ValueError("fixture parser accepts exactly one page")
    if b"/Filter" in data:
        raise ValueError("fixture parser accepts only uncompressed text streams")
    stream_match = re.search(rb"stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL)
    if not stream_match:
        raise ValueError("PDF text stream is missing")
    stream = stream_match.group(1)
    found = re.findall(rb"\(((?:\\.|[^\\)])*)\)\s*Tj", stream)
    lines = [item.decode("ascii") for item in found]
    lines = [line.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\") for line in lines]
    if tuple(lines) != PDF_LINES:
        raise ValueError("PDF fixture content did not match the declared text layer")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "corpus" / "documents" / "p3_contract.pdf",
    )
    args = parser.parse_args()
    digest = build_pdf(args.output)
    print(f"pdf_fixture=created sha256={digest} bytes={args.output.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
