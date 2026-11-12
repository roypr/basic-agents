"""Shared LLM primitives: canonical messages, sinks, SSE parsing, base adapter.

The rest of the application speaks one small canonical dialect:

* ``{"role": "user"|"system", "content": str | list[dict]}``
* ``{"role": "assistant", "content": str, "tool_calls": [...],
   "reasoning_content": str?, "reasoning_details": list?,
   "reasoning_blocks": list?}``
* ``{"role": "tool", "tool_call_id": str, "content": str | list[dict]}``

Provider adapters translate that dialect to/from their wire format.  Keeping
the canonical form close to OpenAI's shape makes the existing agent loop and
session database work unchanged while still allowing Anthropic's block format.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Protocol

from cli.style import dim
from utils.console import safe_write

logger = logging.getLogger("basic_agents")


class ProviderStreamError(RuntimeError):
    """Raised when a provider reports an error inside a streaming response."""


class StreamSink(Protocol):
    """Receives streamed model output so callers can control rendering.

    ``llm_client`` never prints directly when a sink is supplied.  The default
    (:class:`PrintSink`) prints a framed one-shot view; the REPL injects its
    own sink (see :mod:`cli.sink`) for clean turn separation.
    """

    def start(self) -> None:
        """Called once before the first chunk of a stream."""

    def reasoning(self, text: str) -> None:
        """A reasoning/thinking delta."""

    def content(self, text: str) -> None:
        """A visible content delta."""

    def end(self) -> None:
        """Called once after the last chunk of a stream."""


class PrintSink:
    """Default sink: buffers reasoning, streams visible content.

    Reasoning is intentionally *not* written token-by-token.  CJK reasoning
    rendered as individual tokens is noisy and, on legacy Windows consoles,
    can trip encoding errors mid-stream.  Buffering it and emitting one block
    when the visible answer starts keeps the terminal usable.  The block is
    still shown in full (and is always encoding-safe).
    """

    def __init__(self, stream=None):
        self._stream = stream
        self._reasoning_buffer: list[str] = []
        self._content_started = False

    def start(self) -> None:
        self._reasoning_buffer.clear()
        self._content_started = False
        safe_write("\n", self._stream, flush=True)

    def reasoning(self, text: str) -> None:
        if text:
            self._reasoning_buffer.append(text)

    def _flush_reasoning(self) -> None:
        if not self._reasoning_buffer:
            return
        reasoning = "".join(self._reasoning_buffer)
        self._reasoning_buffer.clear()
        safe_write(dim("[Think]\n"), self._stream, flush=True)
        safe_write(reasoning, self._stream, flush=True)
        safe_write(dim("\n[/Think]\n"), self._stream, flush=True)

    def content(self, text: str) -> None:
        if not text:
            return
        if not self._content_started:
            self._flush_reasoning()
            self._content_started = True
        safe_write(text, self._stream, flush=True)

    def end(self) -> None:
        # Tool-call-only turns have reasoning but no visible content; flush it
        # here so the user still sees what the model was thinking.
        self._flush_reasoning()
        safe_write("\n", self._stream, flush=True)


@dataclass
class LLMOptions:
    """Provider-neutral request options.

    ``max_tokens`` is the canonical output budget.  Adapters map it to the
    provider's current field name (OpenAI: ``max_completion_tokens``; Anthropic
    and the OpenAI-compatible providers: ``max_tokens``).
    """

    max_tokens: int | None = None
    reasoning_effort: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    extra: dict = field(default_factory=dict)


EFFORT_ALIASES = {
    "off": "none",
    "disabled": "none",
    "ultra": "max",
}
VALID_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}


def normalize_effort(value: str | None) -> str | None:
    """Normalise a user-facing effort value, or return ``None``."""
    if value is None:
        return None
    effort = str(value).strip().lower()
    effort = EFFORT_ALIASES.get(effort, effort)
    if effort not in VALID_EFFORTS:
        logger.warning("Unknown reasoning effort %r; sending it unchanged", value)
    return effort


def iter_sse(response) -> Iterator[tuple[str | None, str]]:
    """Yield ``(event_name, data)`` pairs from an SSE response.

    Handles the three shapes we encounter in practice:

    * OpenAI/DeepSeek/OpenRouter: repeated ``data: {...}`` lines, often without
      a blank line between events (llama.cpp and some proxies do this).
    * Anthropic: ``event: ...`` followed by ``data: {...}`` and a blank line.
    * Keep-alive comments such as ``: OPENROUTER PROCESSING``.

    Multi-line ``data:`` payloads are uncommon in these APIs; each data line is
    emitted as a complete event when the previous line is already pending.  This
    keeps servers that omit blank separators working without buffering the whole
    stream.
    """
    event: str | None = None
    data_lines: list[str] = []

    def flush():
        nonlocal event, data_lines
        if data_lines:
            payload = "\n".join(data_lines)
            data_lines = []
            name = event
            event = None
            return name, payload
        return None

    for raw_line in response.iter_lines():
        if raw_line is None:
            continue
        line = (
            raw_line.decode("utf-8", errors="replace")
            if isinstance(raw_line, (bytes, bytearray))
            else raw_line
        )

        if line == "":
            item = flush()
            if item:
                yield item
            continue

        if line.startswith(":"):
            continue

        if line.startswith("event:"):
            item = flush()
            if item:
                yield item
            event = line[6:].strip()
            continue

        if line.startswith("data:"):
            item = flush()
            if item:
                yield item
            data_lines.append(line[5:].strip())
            continue

        # Some servers send bare JSON lines without the ``data:`` prefix.
        stripped = line.strip()
        if stripped.startswith("{") or stripped == "[DONE]":
            item = flush()
            if item:
                yield item
            data_lines.append(stripped)

    item = flush()
    if item:
        yield item


def reasoning_details_text(details: Iterable) -> str:
    """Best-effort plain text from OpenRouter ``reasoning_details`` entries."""
    if isinstance(details, dict):
        details = [details]
    parts: list[str] = []
    for detail in details or []:
        if not isinstance(detail, dict):
            continue
        text = detail.get("text") or detail.get("summary")
        if isinstance(text, str) and text:
            parts.append(text)
    return "".join(parts)


class ModelAdapter:
    """Base adapter for OpenAI-compatible chat-completions APIs.

    Subclasses override endpoint/header/payload details.  The shared streaming
    decoder handles the common ``choices[0].delta`` shape used by OpenAI,
    DeepSeek and OpenRouter.
    """

    provider = "openai_compatible"
    max_tokens_field = "max_tokens"

    # ------------------------------------------------------------------ wire
    def endpoint(self, base_url: str) -> str:
        base = (base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def headers(self, api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def build_payload(
        self,
        messages: list,
        model: str,
        tools: list | None = None,
        use_tools: bool = True,
        options: LLMOptions | None = None,
    ) -> dict:
        options = options or LLMOptions()
        payload: dict = {
            "model": model,
            "messages": self.normalize_messages(
                messages, use_tools=bool(use_tools and tools)
            ),
            "stream": True,
        }

        if use_tools and tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if options.max_tokens is not None:
            payload[self.max_tokens_field] = options.max_tokens
        if options.temperature is not None:
            payload["temperature"] = options.temperature
        if options.top_p is not None:
            payload["top_p"] = options.top_p
        if options.extra:
            payload.update(options.extra)

        self.apply_reasoning(payload, options.reasoning_effort)
        return payload

    def normalize_messages(self, messages: list, use_tools: bool = True) -> list:
        """Return messages in the provider's accepted shape.

        The base implementation copies messages and drops internal-only
        metadata.  Subclasses decide which reasoning fields are meaningful.
        """
        cleaned = []
        for message in messages:
            item = dict(message)
            for key in ("reasoning_details", "reasoning_blocks"):
                item.pop(key, None)
            cleaned.append(item)
        return cleaned

    def apply_reasoning(self, payload: dict, effort: str | None) -> None:
        """Hook for providers with a reasoning-control parameter."""

    # --------------------------------------------------------------- stream
    def stream_and_collect(self, response, sink: StreamSink | None = None) -> dict:
        sink = sink or PrintSink()
        sink.start()

        collected_content: list[str] = []
        collected_reasoning: list[str] = []
        collected_details: list = []
        tool_calls_acc: dict[int, dict] = {}
        role = "assistant"
        finish_reason = None

        try:
            for _event, data in iter_sse(response):
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                if isinstance(chunk, dict) and chunk.get("error"):
                    error = chunk["error"]
                    if isinstance(error, dict):
                        error = error.get("message") or error
                    raise ProviderStreamError(str(error))

                choices = chunk.get("choices") or []
                if not choices:
                    continue

                choice = choices[0] or {}
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}

                if delta.get("role"):
                    role = delta["role"]

                reasoning_delta = self._reasoning_delta(delta)
                if reasoning_delta:
                    collected_reasoning.append(reasoning_delta)
                    sink.reasoning(reasoning_delta)

                details_delta = delta.get("reasoning_details")
                if details_delta:
                    if isinstance(details_delta, dict):
                        collected_details.append(details_delta)
                    else:
                        collected_details.extend(details_delta)

                content_delta = self._content_delta(delta)
                if content_delta:
                    collected_content.append(content_delta)
                    sink.content(content_delta)

                for tc_delta in delta.get("tool_calls") or []:
                    idx = tc_delta.get("index", 0)
                    acc = tool_calls_acc.setdefault(
                        idx,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if tc_delta.get("id") and not acc["id"]:
                        acc["id"] = tc_delta["id"]
                    fn = tc_delta.get("function") or {}
                    if fn.get("name"):
                        acc["function"]["name"] += fn["name"]
                    arguments = fn.get("arguments")
                    if isinstance(arguments, str):
                        acc["function"]["arguments"] += arguments
                    elif arguments:
                        acc["function"]["arguments"] += json.dumps(
                            arguments, ensure_ascii=False
                        )
        finally:
            sink.end()

        message: dict = {"role": role, "content": "".join(collected_content)}
        if collected_reasoning:
            message["reasoning_content"] = "".join(collected_reasoning)
        if collected_details:
            message["reasoning_details"] = collected_details
        if tool_calls_acc:
            message["tool_calls"] = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
        if finish_reason:
            message["finish_reason"] = finish_reason
        return message

    def _reasoning_delta(self, delta: dict) -> str:
        value = delta.get("reasoning_content")
        if value is None:
            value = delta.get("reasoning")
        if isinstance(value, dict):
            value = value.get("text") or value.get("summary") or ""
        return value if isinstance(value, str) else ""

    def _content_delta(self, delta: dict) -> str:
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return ""

    # --------------------------------------------------------------- access
    def extract_content(self, message: dict) -> str:
        return message.get("content") or ""

    def extract_reasoning(self, message: dict) -> str:
        text = message.get("reasoning_content") or message.get("reasoning") or ""
        if isinstance(text, dict):
            text = text.get("text") or text.get("summary") or ""
        if not text and message.get("reasoning_details"):
            text = reasoning_details_text(message["reasoning_details"])
        return text if isinstance(text, str) else ""

    def extract_tool_calls(self, message: dict) -> list:
        return message.get("tool_calls") or []

    def build_assistant_message(
        self, content: str, reasoning: str, tool_calls: list
    ) -> dict:
        """Rebuild the canonical assistant message for conversation history."""
        message = {"role": "assistant", "content": content or ""}
        if reasoning:
            message["reasoning_content"] = reasoning
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message
