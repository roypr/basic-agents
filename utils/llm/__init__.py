"""Provider adapters, shared streaming primitives and error normalisation."""

from .anthropic import AnthropicAdapter
from .base import (
    LLMOptions,
    ModelAdapter,
    PrintSink,
    StreamSink,
    iter_sse,
    normalize_effort,
    reasoning_details_text,
)
from .errors import (
    ErrorKind,
    LLMError,
    ProviderStreamError,
    detect_api_error,
    log_error,
    normalize_api_error,
    normalize_network_error,
    render_error,
    retry_after_seconds,
    stream_error,
)
from .openai_compat import DeepSeekAdapter, OpenAIAdapter, OpenRouterAdapter
from .registry import get_adapter

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
    "detect_api_error",
    "get_adapter",
    "iter_sse",
    "log_error",
    "normalize_api_error",
    "normalize_effort",
    "normalize_network_error",
    "reasoning_details_text",
    "render_error",
    "retry_after_seconds",
    "stream_error",
]
