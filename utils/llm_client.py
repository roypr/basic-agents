"""Public LLM client facade.

Provider-specific request construction and stream decoding live in
:mod:`utils.llm`.  This module keeps the historical ``call_llm_streaming``
entry point so agents, tests and utilities have one place to call.

Every failure — HTTP status, transport error, or an error event inside the
stream — is normalised into :class:`~utils.llm.errors.LLMError` here, logged to
``logs/app_errors.log``, and re-raised as that single type.  Callers therefore
only need to handle one exception and can render it with
:func:`~utils.llm.errors.render_error`.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import requests

from utils.http_utils import get_request_session
from utils.llm import (  # re-exported for backward compatibility
    AnthropicAdapter,
    DeepSeekAdapter,
    ErrorKind,
    LLMError,
    LLMOptions,
    ModelAdapter,
    OpenAIAdapter,
    OpenRouterAdapter,
    PrintSink,
    ProviderStreamError,
    StreamSink,
    detect_api_error,
    get_adapter,
    iter_sse,
    log_error,
    normalize_api_error,
    normalize_network_error,
    render_error,
    retry_after_seconds,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from requests import Response

logger = logging.getLogger("basic_agents")

__all__ = [
    "AnthropicAdapter",
    "DeepSeekAdapter",
    "ErrorKind",
    "LLMError",
    "LLMOptions",
    "ModelAdapter",
    "OpenAIAdapter",
    "OpenRouterAdapter",
    "PrintSink",
    "ProviderStreamError",
    "StreamSink",
    "call_llm_streaming",
    "get_adapter",
    "iter_sse",
    "render_error",
]

# Only the head of a non-OK body is enough to explain the failure.
_MAX_BODY_FOR_MESSAGE = 4000


def _provider_label(adapter: ModelAdapter, provider_type: str | None) -> str:
    """User-facing provider name: the configured one, else the wire dialect."""
    return (provider_type or "").strip() or adapter.provider


def _response_text(response: "Response") -> str:
    """Body text of a response, never raising (streams may already be broken)."""
    try:
        return response.text or ""
    except Exception:  # noqa: BLE001 - diagnostics must not mask the real error
        try:
            return (response.content or b"").decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""


def _body_for_error(response: "Response") -> str:
    """Read a failed response body, bounded unless it looks like JSON.

    JSON is kept intact so the envelope still parses; gigantic HTML/plain-text
    proxy pages are cut to a readable head.
    """
    body = _response_text(response)
    if len(body) > _MAX_BODY_FOR_MESSAGE and body.lstrip()[:1] not in ("{", "["):
        return body[:_MAX_BODY_FOR_MESSAGE]
    return body


def _ok_response_error(
    response: "Response",
    *,
    provider: str,
    model: str,
    url: str,
) -> LLMError | None:
    """Detect a 200 that is really an error (proxy / non-streaming JSON).

    Only ``application/json`` bodies are inspected: reading the body of an SSE
    response would consume the stream the decoder still has to read.  Test
    doubles without headers are left to the decoder as well.
    """
    headers = getattr(response, "headers", {}) or {}
    content_type = str(
        headers.get("Content-Type") or headers.get("content-type") or ""
    ).lower()
    if "json" not in content_type or "event-stream" in content_type:
        return None

    body = _response_text(response)
    if not body.strip():
        return LLMError(
            "Provider returned an empty response body.",
            kind=ErrorKind.EMPTY_RESPONSE,
            provider=provider,
            model=model,
            status_code=getattr(response, "status_code", None),
            url=url,
        )
    return detect_api_error(
        body,
        provider=provider,
        model=model,
        status_code=getattr(response, "status_code", None),
        url=url,
    )


def call_llm_streaming(
    messages: list,
    model: str,
    llm_base: str,
    api_key: str,
    adapter: ModelAdapter | None = None,
    use_tools: bool = True,
    tools: list | None = None,
    sink: StreamSink | None = None,
    *,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    extra: dict | None = None,
    provider_type: str | None = None,
) -> dict:
    """Stream one completion and return the canonical assistant message.

    ``max_tokens`` is the provider-neutral output budget; each adapter maps it
    to the field its API currently expects.  Only explicitly supplied sampling
    parameters are sent, so reasoning models are not forced onto unsupported
    values.

    Raises:
        LLMError: for any provider, transport or streaming failure.  The raw
            body is on ``error.raw`` and has been written to the error log.
    """
    adapter = adapter or get_adapter(
        model, provider_name=provider_type, api_base_url=llm_base
    )
    options = LLMOptions(
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        top_p=top_p,
        extra=dict(extra or {}),
    )

    payload = adapter.build_payload(
        messages, model, tools=tools, use_tools=use_tools, options=options
    )
    url = adapter.endpoint(llm_base)
    headers = adapter.headers(api_key)
    provider = _provider_label(adapter, provider_type)

    logger.debug("LLM URL: %s", url)
    logger.debug("LLM Payload:\n%s", json.dumps(payload, indent=2, ensure_ascii=False))

    try:
        response = get_request_session(api_key=api_key).post(
            url,
            json=payload,
            headers=headers,
            stream=True,
            timeout=1200,
        )
    except requests.RequestException as exc:
        error = normalize_network_error(exc, provider=provider, model=model, url=url)
        log_error(error)
        raise error from exc

    logger.debug("Status: %s", response.status_code)
    logger.debug("Response headers: %s", dict(response.headers))

    if not getattr(response, "ok", True):
        error = normalize_api_error(
            _body_for_error(response),
            provider=provider,
            model=model,
            status_code=getattr(response, "status_code", None),
            url=url,
            retry_after=retry_after_seconds(getattr(response, "headers", {})),
        )
        log_error(error)
        raise error

    error = _ok_response_error(response, provider=provider, model=model, url=url)
    if error is not None:
        log_error(error)
        raise error

    try:
        return adapter.stream_and_collect(response, sink)
    except LLMError as exc:
        # Adapters know the wire dialect but not the configured provider/model.
        exc.provider = exc.provider or provider
        exc.model = exc.model or model
        exc.url = exc.url or url
        log_error(exc)
        raise
    except requests.RequestException as exc:
        # Connection reset / broken chunk while reading the SSE stream.
        error = normalize_network_error(
            exc,
            provider=provider,
            model=model,
            url=url,
            kind="stream",
        )
        log_error(error)
        raise error from exc
