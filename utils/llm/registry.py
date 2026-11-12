"""Provider adapter registry and inference."""

from __future__ import annotations

from .anthropic import AnthropicAdapter
from .base import ModelAdapter
from .openai_compat import DeepSeekAdapter, OpenAIAdapter, OpenRouterAdapter

_ADAPTERS = {
    "anthropic": AnthropicAdapter,
    "claude": AnthropicAdapter,
    "deepseek": DeepSeekAdapter,
    "openai": OpenAIAdapter,
    "openrouter": OpenRouterAdapter,
}


def get_adapter(
    model_name: str = "",
    provider_name: str | None = None,
    api_base_url: str | None = None,
) -> ModelAdapter:
    """Resolve the adapter for a provider, base URL, or model name.

    Explicit provider config wins.  Base URL and model-name inference keep
    ``--llm-base`` one-off runs and older ``providers.json`` files working
    without requiring a ``type`` field.
    """
    key = (provider_name or "").strip().lower()
    if key in _ADAPTERS:
        return _ADAPTERS[key]()
    if key in ("openai_compatible", "openai-compatible", "generic", "local"):
        # Explicit opt-out of provider-specific inference.
        return ModelAdapter()

    base = (api_base_url or "").lower()
    if "anthropic.com" in base or "claude" in base:
        return AnthropicAdapter()
    if "openrouter.ai" in base:
        return OpenRouterAdapter()
    if "api.openai.com" in base:
        return OpenAIAdapter()
    if "api.deepseek.com" in base:
        return DeepSeekAdapter()

    model = (model_name or "").lower()
    if model.startswith("claude") or "/claude" in model:
        return AnthropicAdapter()
    if model.startswith("deepseek") or "deepseek" in model:
        return DeepSeekAdapter()
    if model.startswith(("gpt-", "gpt", "o1", "o3", "o4", "chatgpt")):
        return OpenAIAdapter()
    if "/" in model and base == "":
        # OpenRouter model IDs are namespaced (vendor/model).
        return OpenRouterAdapter()

    return ModelAdapter()
