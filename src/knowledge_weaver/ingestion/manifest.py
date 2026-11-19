from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from knowledge_weaver.core.ids import document_id, sha256_text, source_version_id
from knowledge_weaver.models import (
    FileManifestEntry,
    ParseLimits,
    RejectedFile,
    Source,
    SourceVersion,
)

from .fingerprints import parser_fingerprint
from .paths import (
    PathSafetyError,
    SUPPORTED_SUFFIXES,
    normalize_root,
    is_link_or_reparse,
    resolve_relative_file,
    sha256_file,
)

_IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    "dist",
    "build",
}


@dataclass(frozen=True)
class SourceChanges:
    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManifestResult:
    source_version: SourceVersion | None
    changes: SourceChanges
    rejected_files: tuple[RejectedFile, ...] = ()
    warnings: tuple[str, ...] = ()


class ManifestBuilder:
    """Build a deterministic, path-safe file version for one source root."""

    def build(
        self,
        source: Source,
        allowed_root: str | Path,
        limits: ParseLimits | None = None,
        *,
        previous: SourceVersion | None = None,
        revision: str | None = None,
        dirty: bool | None = None,
    ) -> ManifestResult:
        limits = limits or ParseLimits()
        rejected: list[RejectedFile] = []
        warnings: list[str] = []
        try:
            root = normalize_root(allowed_root)
        except PathSafetyError as exc:
            return ManifestResult(
                None,
                SourceChanges(),
                (RejectedFile(path=".", code="PATH_DENIED", message=str(exc)),),
            )

        candidates, traversal_rejections = self._enumerate(root, source)
        rejected.extend(traversal_rejections)
        if len(candidates) > limits.max_files:
            return ManifestResult(
                None,
                SourceChanges(),
                tuple(rejected)
                + (
                    RejectedFile(
                        path=".",
                        code="LIMIT_EXCEEDED",
                        message=f"Source contains more than {limits.max_files} supported files.",
                    ),
                ),
                ("File scan stopped because the source exceeded the configured file-count limit.",),
            )

        fingerprint = parser_fingerprint(source.kind)
        entries: list[FileManifestEntry] = []
        seen: set[str] = set()
        for candidate in candidates:
            relative = candidate.relative_to(root).as_posix()
            try:
                normalized, resolved = resolve_relative_file(root, relative)
            except PathSafetyError as exc:
                rejected.append(
                    RejectedFile(path=relative, code="PATH_DENIED", message=str(exc))
                )
                continue

            path_key = normalized.casefold()
            if path_key in seen:
                rejected.append(
                    RejectedFile(
                        path=normalized,
                        code="PATH_COLLISION",
                        message="Path collides with another source path under case-insensitive normalization.",
                    )
                )
                continue
            seen.add(path_key)

            try:
                size = resolved.stat().st_size
                file_limit = min(
                    limits.max_file_bytes,
                    limits.max_python_file_bytes
                    if source.kind.value == "python_repo"
                    else limits.max_file_bytes,
                )
                if size > file_limit:
                    rejected.append(
                        RejectedFile(
                            path=normalized,
                            code="LIMIT_EXCEEDED",
                            message=f"File exceeds the configured {file_limit}-byte limit.",
                        )
                    )
                    continue
                raw_hash = sha256_file(resolved)
            except OSError:
                rejected.append(
                    RejectedFile(
                        path=normalized,
                        code="PARSE_FAILED",
                        message="File could not be read while building its manifest.",
                    )
                )
                continue

            entries.append(
                FileManifestEntry(
                    relative_path=normalized,
                    raw_hash=raw_hash,
                    document_id=document_id(source.id, normalized, raw_hash, fingerprint),
                )
            )

        if revision is None or dirty is None:
            detected_revision, detected_dirty, git_warning = _git_state(root)
            if git_warning:
                warnings.append(git_warning)
        else:
            detected_revision, detected_dirty = revision, dirty
        resolved_revision = revision if revision is not None else detected_revision
        resolved_dirty = dirty if dirty is not None else detected_dirty
        if previous is not None and previous.source_id != source.id:
            raise ValueError("previous SourceVersion belongs to another source")

        entries.sort(key=lambda item: (item.relative_path.casefold(), item.relative_path))
        manifest_payload = {
            "source_id": source.id,
            "source_kind": source.kind.value,
            "revision": resolved_revision,
            "dirty": resolved_dirty,
            "parser_fingerprint": fingerprint,
            "files": [item.model_dump(mode="json") for item in entries],
        }
        manifest_hash = sha256_text(
            json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        version = SourceVersion(
            id=source_version_id(source.id, manifest_hash),
            source_id=source.id,
            manifest_hash=manifest_hash,
            revision=resolved_revision,
            dirty=resolved_dirty,
            files=entries,
        )
        changes = _changes(previous, version)
        return ManifestResult(
            version,
            changes,
            tuple(rejected),
            tuple(warnings),
        )

    def _enumerate(
        self, root: Path, source: Source
    ) -> tuple[list[Path], list[RejectedFile]]:
        supported = SUPPORTED_SUFFIXES[source.kind]
        candidates: list[Path] = []
        rejected: list[RejectedFile] = []
        unsupported_count = 0

        def on_walk_error(error: OSError) -> None:
            nonlocal rejected
            raw_path = Path(error.filename) if error.filename else root
            try:
                relative = raw_path.relative_to(root).as_posix()
            except ValueError:
                relative = "."
            rejected.append(
                RejectedFile(
                    path=relative or ".",
                    code="PATH_DENIED",
                    message="A source directory could not be inspected.",
                )
            )

        for directory, directories, filenames in os.walk(
            root, topdown=True, followlinks=False, onerror=on_walk_error
        ):
            parent = Path(directory)
            kept_directories: list[str] = []
            for name in sorted(directories, key=str.casefold):
                path = parent / name
                if name.casefold() in _IGNORED_DIRECTORIES:
                    continue
                if is_link_or_reparse(path):
                    relative = path.relative_to(root).as_posix()
                    rejected.append(
                        RejectedFile(
                            path=relative,
                            code="PATH_DENIED",
                            message="Linked directories are not traversed.",
                        )
                    )
                    continue
                kept_directories.append(name)
            directories[:] = kept_directories

            for name in sorted(filenames, key=str.casefold):
                candidate = parent / name
                suffix = candidate.suffix.casefold()
                if suffix not in supported:
                    unsupported_count += 1
                    continue
                candidates.append(candidate)

        candidates.sort(key=lambda item: item.relative_to(root).as_posix().casefold())
        if unsupported_count:
            expected = ", ".join(sorted(supported))
            rejected.append(
                RejectedFile(
                    path=".",
                    code="UNSUPPORTED_FORMAT",
                    message=(
                        f"Ignored {unsupported_count} files outside the source format set ({expected})."
                    ),
                )
            )
        return candidates, rejected


def _changes(previous: SourceVersion | None, current: SourceVersion) -> SourceChanges:
    if previous is None:
        return SourceChanges(added=tuple(item.relative_path for item in current.files))
    previous_by_path = {item.relative_path.casefold(): item for item in previous.files}
    current_by_path = {item.relative_path.casefold(): item for item in current.files}
    added = sorted(
        (item.relative_path for key, item in current_by_path.items() if key not in previous_by_path),
        key=str.casefold,
    )
    deleted = sorted(
        (item.relative_path for key, item in previous_by_path.items() if key not in current_by_path),
        key=str.casefold,
    )
    changed: list[str] = []
    unchanged: list[str] = []
    for key, current_entry in current_by_path.items():
        prior = previous_by_path.get(key)
        if prior is None:
            continue
        if (
            prior.raw_hash != current_entry.raw_hash
            or prior.document_id != current_entry.document_id
        ):
            changed.append(current_entry.relative_path)
        else:
            unchanged.append(current_entry.relative_path)
    return SourceChanges(
        added=tuple(sorted(added, key=str.casefold)),
        changed=tuple(sorted(changed, key=str.casefold)),
        unchanged=tuple(sorted(unchanged, key=str.casefold)),
        deleted=tuple(sorted(deleted, key=str.casefold)),
    )


def _git_state(root: Path) -> tuple[str | None, bool, str | None]:
    command = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(root),
    ]
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        revision_result = subprocess.run(
            [*command, "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            env=env,
        )
        if revision_result.returncode != 0:
            return None, True, "Git revision metadata was unavailable; the source is marked dirty."
        status_result = subprocess.run(
            [*command, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=all"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
            env=env,
        )
        if status_result.returncode != 0:
            return revision_result.stdout.strip(), True, "Git dirty status was unavailable; the source is marked dirty."
        return revision_result.stdout.strip(), bool(status_result.stdout.strip()), None
    except (OSError, subprocess.TimeoutExpired):
        return None, True, "Git metadata could not be read; the source is marked dirty."
