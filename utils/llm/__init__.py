"""Provider adapters and shared streaming primitives."""

from .anthropic import AnthropicAdapter
from .base import (
    LLMOptions,
    ModelAdapter,
    PrintSink,
    ProviderStreamError,
    StreamSink,
    iter_sse,
    normalize_effort,
    reasoning_details_text,
)
from .openai_compat import DeepSeekAdapter, OpenAIAdapter, OpenRouterAdapter
from .registry import get_adapter

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
    "get_adapter",
    "iter_sse",
    "normalize_effort",
    "reasoning_details_text",
]
