"""Public LLM client facade.

Provider-specific request construction and stream decoding live in
:mod:`utils.llm`.  This module keeps the historical ``call_llm_streaming``
entry point so agents, tests and utilities have one place to call.
"""

from __future__ import annotations

import json
import logging

from utils.http_utils import get_request_session
from utils.llm import (  # re-exported for backward compatibility
    AnthropicAdapter,
    DeepSeekAdapter,
    LLMOptions,
    ModelAdapter,
    OpenAIAdapter,
    OpenRouterAdapter,
    PrintSink,
    ProviderStreamError,
    StreamSink,
    get_adapter,
    iter_sse,
)

logger = logging.getLogger("basic_agents")

__all__ = [
    "AnthropicAdapter",
    "DeepSeekAdapter",
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
]


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

    logger.debug("LLM URL: %s", url)
    logger.debug("LLM Payload:\n%s", json.dumps(payload, indent=2, ensure_ascii=False))

    response = get_request_session(api_key=api_key).post(
        url,
        json=payload,
        headers=headers,
        stream=True,
        timeout=1200,
    )
    logger.debug("Status: %s", response.status_code)
    logger.debug("Response headers: %s", dict(response.headers))
    if not getattr(response, "ok", True):
        logger.error("Raw response body: %s", response.text)
    response.raise_for_status()
    return adapter.stream_and_collect(response, sink)
