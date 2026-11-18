from __future__ import annotations

import pytest

from verification.environment.python.validation_safety import redact_secrets, require_setting


def test_missing_configuration_names_setting_without_echoing_value() -> None:
    with pytest.raises(RuntimeError, match="Missing required setting: KW_TEST_REQUIRED"):
        require_setting("KW_TEST_REQUIRED", {})


def test_redaction_masks_tokens_and_connection_credentials() -> None:
    message = (
        "api_key=sample-value password:sample-pass "
        "postgresql://sample-user:sample-pass@localhost:5432/test "
        "Bearer sample-token"
    )

    redacted = redact_secrets(message)

    assert "sample-value" not in redacted
    assert "sample-pass" not in redacted
    assert "sample-token" not in redacted
    assert "[REDACTED]" in redacted
