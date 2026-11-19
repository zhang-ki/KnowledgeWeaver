from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from knowledge_weaver.core.errors import ErrorEnvelope

from . import contracts
from .contracts import SchemaModel
from .contracts import __all__ as contract_names

MODEL_TYPES: tuple[type[BaseModel], ...] = tuple(
    getattr(contracts, name)
    for name in contract_names
    if isinstance(getattr(contracts, name), type)
    and issubclass(getattr(contracts, name), SchemaModel)
    and getattr(contracts, name) is not SchemaModel
) + (ErrorEnvelope,)


def json_schema_registry() -> dict[str, dict[str, Any]]:
    return {model.__name__: model.model_json_schema() for model in MODEL_TYPES}


def write_json_schemas(directory: str | Path) -> list[Path]:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, schema in sorted(json_schema_registry().items()):
        path = target / f"{name}.schema.json"
        path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths
