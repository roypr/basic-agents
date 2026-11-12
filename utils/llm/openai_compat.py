"""OpenAI, DeepSeek and OpenRouter adapters.

All three speak the Chat Completions shape, but they differ in the names and
semantics of their request fields.  Keeping the differences explicit here
avoids the old behaviour of sending llama.cpp-specific parameters to every
remote provider.
"""

from __future__ import annotations

from .base import ModelAdapter, normalize_effort


class OpenAIAdapter(ModelAdapter):
    """Official OpenAI Chat Completions API."""

    provider = "openai"
    # ``max_tokens`` is deprecated for OpenAI; reasoning models reject it.
    max_tokens_field = "max_completion_tokens"

    def endpoint(self, base_url: str) -> str:
        base = (base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1") or base.rsplit("/", 1)[-1].startswith("v"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def normalize_messages(self, messages: list, use_tools: bool = True) -> list:
        cleaned = []
        for message in messages:
            item = dict(message)
            # OpenAI does not accept reasoning fields in chat history.  Do not
            # leak DeepSeek/OpenRouter/Anthropic metadata into the request.
            for key in (
                "reasoning_content",
                "reasoning",
                "reasoning_details",
                "reasoning_blocks",
            ):
                item.pop(key, None)
            # With o-series and newer, developer messages replace system.
            if item.get("role") == "system":
                item["role"] = "developer"
            cleaned.append(item)
        return cleaned

    def apply_reasoning(self, payload: dict, effort: str | None) -> None:
        effort = normalize_effort(effort)
        if effort:
            payload["reasoning_effort"] = effort


class DeepSeekAdapter(ModelAdapter):
    """DeepSeek's OpenAI-compatible API (thinking mode)."""

    provider = "deepseek"
    max_tokens_field = "max_tokens"

    # DeepSeek documents low/high/max; map the wider OpenAI vocabulary onto it.
    _EFFORT_MAP = {
        "minimal": "low",
        "medium": "high",
        "xhigh": "high",
        "ultra": "max",
    }

    def endpoint(self, base_url: str) -> str:
        base = (base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def normalize_messages(self, messages: list, use_tools: bool = True) -> list:
        cleaned = []
        for message in messages:
            item = dict(message)
            item.pop("reasoning_details", None)
            item.pop("reasoning_blocks", None)
            # DeepSeek requires prior reasoning_content when tools are in the
            # request; without tools it is ignored, so omit the extra payload.
            if not use_tools:
                item.pop("reasoning_content", None)
            item.pop("reasoning", None)
            cleaned.append(item)
        return cleaned

    def apply_reasoning(self, payload: dict, effort: str | None) -> None:
        effort = normalize_effort(effort)
        if not effort:
            return
        if effort == "none":
            payload["thinking"] = {"type": "disabled"}
            return
        payload["reasoning_effort"] = self._EFFORT_MAP.get(effort, effort)


class OpenRouterAdapter(ModelAdapter):
    """OpenRouter's OpenAI-compatible API with its unified reasoning object."""

    provider = "openrouter"
    max_tokens_field = "max_tokens"

    def endpoint(self, base_url: str) -> str:
        base = (base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/api/v1") or base.endswith("/v1"):
            return f"{base}/chat/completions"
        if "openrouter.ai" in base and base.count("/") <= 3:
            return f"{base}/api/v1/chat/completions"
        return f"{base}/chat/completions"

    def normalize_messages(self, messages: list, use_tools: bool = True) -> list:
        cleaned = []
        for message in messages:
            item = dict(message)
            item.pop("reasoning_blocks", None)
            reasoning_content = item.pop("reasoning_content", None)
            # OpenRouter accepts the structured ``reasoning_details`` (and the
            # legacy ``reasoning`` string).  Prefer details when present.
            if not item.get("reasoning_details") and not item.get("reasoning"):
                if reasoning_content:
                    item["reasoning"] = reasoning_content
            cleaned.append(item)
        return cleaned

    def apply_reasoning(self, payload: dict, effort: str | None) -> None:
        effort = normalize_effort(effort)
        if effort:
            payload["reasoning"] = {"effort": effort}
