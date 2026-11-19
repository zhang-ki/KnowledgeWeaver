from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from knowledge_weaver.core.errors import ProviderUnavailable


class JsonTransport(Protocol):
    async def post_json(
        self,
        endpoint_url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        timeout_seconds: float,
        local_only: bool = False,
    ) -> dict[str, Any]: ...


class HttpxJsonTransport:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def post_json(
        self,
        endpoint_url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        timeout_seconds: float,
        local_only: bool = False,
    ) -> dict[str, Any]:
        owned_client = self._client is None
        client = self._client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=timeout_seconds,
        )
        try:
            response = await client.post(
                endpoint_url,
                json=payload,
                headers=headers,
                timeout=timeout_seconds,
                follow_redirects=False,
            )
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            raise ProviderUnavailable(
                "provider request timed out",
                category="timeout",
                retryable=True,
            ) from None
        except (httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.LocalProtocolError, TypeError, ValueError):
            raise ProviderUnavailable(
                "provider request was invalid",
                category="request_invalid",
                retryable=False,
            ) from None
        except (httpx.NetworkError, httpx.RemoteProtocolError):
            raise ProviderUnavailable(
                "provider connection failed",
                category="network_error",
                retryable=True,
            ) from None
        finally:
            if owned_client:
                await client.aclose()

        status = response.status_code
        if status >= 400:
            retryable = status in {408, 425, 429} or 500 <= status <= 599
            if status == 429:
                category = "rate_limited"
            elif status in {408, 425} or 500 <= status <= 599:
                category = "transient_http"
            elif status in {401, 403}:
                category = "authentication_failed"
            else:
                category = "request_rejected"
            raise ProviderUnavailable(
                "provider rejected the request",
                category=category,
                retryable=retryable,
                http_status=status,
            )
        try:
            body = response.json()
        except (ValueError, UnicodeDecodeError):
            raise ProviderUnavailable(
                "provider response was not valid JSON",
                category="response_invalid",
                retryable=False,
            ) from None
        if not isinstance(body, dict):
            raise ProviderUnavailable(
                "provider response must be a JSON object",
                category="response_invalid",
                retryable=False,
            )
        return body
