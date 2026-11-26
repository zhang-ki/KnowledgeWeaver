from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from knowledge_weaver.core.config import RuntimeSettings
from knowledge_weaver.core.errors import ErrorCode, KnowledgeWeaverError
from knowledge_weaver.ingestion.paths import PathSafetyError, normalize_root
from knowledge_weaver.providers.profiles import LocalQwenProfile, OpenAIChatProfile

_CORPUS_NAMESPACE = UUID("256bf6b7-a9db-5eac-944e-985ef155c9fe")


def _loopback_endpoint(value: str, *, name: str, path_suffix: str | None = None) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname
    try:
        is_loopback = bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        is_loopback = host == "localhost"
    if (
        parsed.scheme != "http"
        or not is_loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (path_suffix is not None and parsed.path != path_suffix)
    ):
        raise ValueError(f"{name} must use a loopback HTTP endpoint")
    return value


def _corpus_id(value: str | None) -> str:
    if value is None or not value.strip():
        return str(uuid5(_CORPUS_NAMESPACE, "knowledgeweaver.local-product"))
    try:
        return str(UUID(value.strip()))
    except ValueError as exc:
        raise ValueError("KW_CORPUS_ID must be a UUID") from exc


def _allowed_roots(value: str | None) -> tuple[Path, ...]:
    roots: list[Path] = []
    for item in (value or "").split(os.pathsep):
        if not item.strip():
            continue
        try:
            roots.append(normalize_root(item.strip()))
        except PathSafetyError as exc:
            raise ValueError("KW_ALLOWED_ROOTS must contain existing, real directories") from exc
    return tuple(roots)


def _local_chat_profile(env: dict[str, str]) -> tuple[str, str] | None:
    endpoint = env.get("KW_LOCAL_CHAT_URL", "").strip()
    model = env.get("KW_LOCAL_CHAT_MODEL", "").strip()
    if not endpoint and not model:
        return None
    if not endpoint or not model:
        raise ValueError("KW_LOCAL_CHAT_URL and KW_LOCAL_CHAT_MODEL must be set together")
    _loopback_endpoint(
        endpoint,
        name="KW_LOCAL_CHAT_URL",
        path_suffix="/v1/chat/completions",
    )
    return endpoint, model


def _remote_chat_profile(env: dict[str, str], settings: RuntimeSettings) -> OpenAIChatProfile | None:
    if not settings.allow_remote or settings.model_offline:
        return None
    base_url = env.get("KW_LLM_BASE_URL", "").strip()
    model = env.get("KW_LLM_MODEL", "").strip()
    api_key = env.get("KW_LLM_API_KEY", "").strip()
    if not base_url and not model and not api_key:
        return None
    profile = OpenAIChatProfile.from_environment(env)
    return profile.model_copy(update={"timeout_seconds": 60.0})


@dataclass(frozen=True, slots=True)
class AppConfig:
    database_url: str
    corpus_id: str
    data_dir: Path
    allowed_roots: tuple[Path, ...]
    qdrant_url: str | None
    qdrant_api_key: str | None
    embedding_profile: LocalQwenProfile | None
    local_chat_endpoint: str | None
    local_chat_model: str | None
    runtime_settings: RuntimeSettings = field(default_factory=RuntimeSettings)
    remote_chat_profile: OpenAIChatProfile | None = None

    @classmethod
    def from_environment(cls, values: dict[str, str] | None = None) -> AppConfig:
        env = dict(os.environ if values is None else values)
        database_url = env.get("KW_DATABASE_URL", "").strip()
        if not database_url:
            raise KnowledgeWeaverError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Local PostgreSQL is not configured.",
                details={"category": "configuration_missing", "setting": "KW_DATABASE_URL"},
            )
        parsed_database = urlsplit(database_url)
        host = parsed_database.hostname
        try:
            database_is_loopback = bool(host and ipaddress.ip_address(host).is_loopback)
        except ValueError:
            database_is_loopback = host == "localhost"
        if parsed_database.scheme not in {"postgresql", "postgresql+psycopg"} or not database_is_loopback:
            raise ValueError("KW_DATABASE_URL must point to a loopback PostgreSQL service")

        qdrant_url = env.get("KW_QDRANT_URL", "").strip() or None
        if qdrant_url is not None:
            _loopback_endpoint(qdrant_url, name="KW_QDRANT_URL")
        qdrant_api_key = env.get("KW_QDRANT_API_KEY", "").strip() or None

        embedding_profile: LocalQwenProfile | None = None
        has_embedding_endpoint = bool(env.get("KW_EMBEDDING_BASE_URL", "").strip())
        has_embedding_model = bool(env.get("KW_EMBEDDING_MODEL", "").strip())
        if has_embedding_endpoint != has_embedding_model:
            raise ValueError("KW_EMBEDDING_BASE_URL and KW_EMBEDDING_MODEL must be set together")
        if has_embedding_endpoint and qdrant_url:
            embedding_profile = LocalQwenProfile.from_environment(env)

        runtime_settings = RuntimeSettings.from_environment(env)
        chat_profile = _local_chat_profile(env)
        remote_chat_profile = _remote_chat_profile(env, runtime_settings)
        data_dir = Path(env.get("KW_DATA_DIR", str(Path.cwd() / ".kw" / "app-data"))).expanduser()
        if not data_dir.is_absolute():
            raise ValueError("KW_DATA_DIR must be an absolute local path")
        data_dir = data_dir.resolve(strict=False)

        return cls(
            database_url=database_url,
            corpus_id=_corpus_id(env.get("KW_CORPUS_ID")),
            data_dir=data_dir,
            allowed_roots=_allowed_roots(env.get("KW_ALLOWED_ROOTS")),
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            embedding_profile=embedding_profile,
            local_chat_endpoint=chat_profile[0] if chat_profile else None,
            local_chat_model=chat_profile[1] if chat_profile else None,
            runtime_settings=runtime_settings,
            remote_chat_profile=remote_chat_profile,
        )
