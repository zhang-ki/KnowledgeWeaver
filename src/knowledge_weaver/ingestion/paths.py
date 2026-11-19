from __future__ import annotations

import os
import stat
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from knowledge_weaver.models import SourceKind

_REPARSE_POINT = 0x400
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 1_000

SUPPORTED_SUFFIXES: dict[SourceKind, frozenset[str]] = {
    SourceKind.python_repo: frozenset({".py"}),
    SourceKind.pdf: frozenset({".pdf"}),
    SourceKind.docx: frozenset({".docx"}),
    SourceKind.markdown: frozenset({".md", ".markdown"}),
}


class PathSafetyError(ValueError):
    """A source path is outside the assigned private root or cannot be trusted."""


class ArchiveSafetyError(ValueError):
    """A document archive violates the bounded DOCX archive policy."""

    def __init__(self, message: str, code: str = "LIMIT_EXCEEDED") -> None:
        super().__init__(message)
        self.code = code


def normalize_relative_path(value: str) -> str:
    if not value or "\x00" in value:
        raise PathSafetyError("relative path is empty or contains a NUL byte")
    normalized_separators = value.replace("\\", "/")
    posix = PurePosixPath(normalized_separators)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise PathSafetyError("absolute paths are not allowed")
    parts = normalized_separators.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PathSafetyError("relative path contains an invalid component")
    if any(":" in part for part in parts):
        raise PathSafetyError("drive and alternate-stream paths are not allowed")
    return "/".join(unicodedata.normalize("NFC", part) for part in parts)


def normalize_root(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise PathSafetyError("allowed_root must be an absolute path")
    try:
        root = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathSafetyError("allowed_root does not resolve to an existing directory") from exc
    if not root.is_dir() or _is_reparse_point(candidate):
        raise PathSafetyError("allowed_root must be a real directory, not a link")
    return root


def resolve_relative_file(root: Path, value: str) -> tuple[str, Path]:
    relative = normalize_relative_path(value)
    current = root
    try:
        for part in PurePosixPath(relative).parts:
            current = current / part
            if _is_reparse_point(current):
                raise PathSafetyError("linked files and directories are not allowed")
        resolved = current.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PathSafetyError("source file is missing") from exc
    except (OSError, RuntimeError) as exc:
        raise PathSafetyError("source path cannot be resolved") from exc
    if not _is_within(root, resolved) or not resolved.is_file():
        raise PathSafetyError("source path is not a regular file under allowed_root")
    return relative, resolved


def is_link_or_reparse(path: Path) -> bool:
    return _is_reparse_point(path)


def is_supported_path(source_kind: SourceKind, relative_path: str) -> bool:
    return Path(relative_path).suffix.casefold() in SUPPORTED_SUFFIXES[source_kind]


def validate_docx_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ArchiveSafetyError("DOCX archive is invalid", code="PARSE_FAILED") from exc

    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ArchiveSafetyError("DOCX archive exceeds the member-count limit")

    total_size = 0
    for member in members:
        name = member.filename.replace("\\", "/")
        path_parts = name.rstrip("/").split("/") if name.rstrip("/") else []
        if (
            not name
            or name.startswith("/")
            or PureWindowsPath(member.filename).drive
            or any(part in {"", "..", "."} for part in path_parts)
        ):
            raise ArchiveSafetyError("DOCX archive contains an unsafe member path", code="PATH_DENIED")
        if member.flag_bits & 0x1:
            raise ArchiveSafetyError(
                "encrypted DOCX archives are not supported", code="UNSUPPORTED_FORMAT"
            )
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ArchiveSafetyError("DOCX archive contains a symbolic link", code="PATH_DENIED")

        total_size += member.file_size
        if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ArchiveSafetyError("DOCX archive exceeds the uncompressed-size limit")
        if member.file_size and member.compress_size == 0:
            raise ArchiveSafetyError("DOCX archive has an invalid compression ratio")
        if member.compress_size:
            ratio = member.file_size / member.compress_size
            if ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
                raise ArchiveSafetyError("DOCX archive exceeds the compression-ratio limit")


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & _REPARSE_POINT)


def _is_within(root: Path, child: Path) -> bool:
    root_value = os.path.normcase(str(root))
    child_value = os.path.normcase(str(child))
    try:
        return os.path.commonpath((root_value, child_value)) == root_value
    except ValueError:
        return False
