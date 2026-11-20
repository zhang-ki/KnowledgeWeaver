"""Command-line recomputation for a saved B07 matrix and per-query JSONL."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from knowledge_weaver.evaluation.reporting import recompute_report_files


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recompute a B07.final Markdown report")
    parser.add_argument("--matrix", type=Path, required=True, help="saved matrix_manifest.json")
    parser.add_argument("--per-query", type=Path, required=True, help="saved per_query.jsonl")
    parser.add_argument("--output", type=Path, required=True, help="Markdown report output path")
    arguments = parser.parse_args(argv)
    report = recompute_report_files(arguments.matrix, arguments.per_query, arguments.output)
    print(
        json.dumps(
            {
                "status": report["status"],
                "query_record_count": report["query_record_count"],
                "report_path": str(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
