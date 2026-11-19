from __future__ import annotations

import os
from collections.abc import Mapping
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .errors import ProviderUnavailable


class RuntimeSettings(BaseModel):
    allow_remote: bool = False
    model_offline: bool = True
    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def from_environment(cls, values: Mapping[str, str] | None = None) -> RuntimeSettings:
        env = os.environ if values is None else values

        def flag(name: str, default: bool) -> bool:
            raw = env.get(name)
            if raw is None:
                return default
            normalized = raw.strip().casefold()
            if normalized in {"true", "1"}:
                return True
            if normalized in {"false", "0"}:
                return False
            raise ValueError(f"{name} must be true or false")

        return cls(
            allow_remote=flag("KW_ALLOW_REMOTE", False),
            model_offline=flag("KW_MODEL_OFFLINE", True),
        )


class RemoteUseApproval(BaseModel):
    external_data_approved: bool = False
    budget_approved: bool = False
    calls_explicitly_enabled: bool = False
    currency_cap: Decimal | None = Field(default=None, gt=Decimal(0))
    model_config = ConfigDict(extra="forbid", frozen=True)

    def require_open(self, settings: RuntimeSettings) -> None:
        if not (
            self.external_data_approved
            and self.budget_approved
            and self.calls_explicitly_enabled
            and settings.allow_remote
            and not settings.model_offline
        ):
            raise ProviderUnavailable(
                "external provider gate is closed",
                category="approval_gate",
                retryable=False,
            )
