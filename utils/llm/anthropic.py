"""Anthropic Messages API adapter.

The Messages API is not an OpenAI shim: it uses ``x-api-key`` auth, a top-level
``system`` prompt, required ``max_tokens``, flat tool schemas, ``tool_use`` /
``tool_result`` content blocks, and a typed SSE event stream.  This adapter
owns all of that translation so the agent loop can stay provider-neutral.

Prompt caching is applied here too: ``cache_control`` breakpoints on the last
tool definition, the system prompt and the newest message let follow-up turns
read the previous prefix instead of paying for it again.
"""

from __future__ import annotations

import json
import logging

from .base import (
    LLMOptions,
    ModelAdapter,
    PrintSink,
    StreamSink,
    iter_sse,
    normalize_effort,
)
from .errors import stream_error

logger = logging.getLogger("basic_agents")

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 8192

# `cache_control` is only valid on cacheable block types.  Thinking blocks and
# empty text blocks are rejected by the API, so breakpoints skip them.
CACHEABLE_BLOCK_TYPES = frozenset(
    {"text", "image", "document", "tool_use", "tool_result"}
)


# Anthropic supports low/medium/high/xhigh/max.  ``minimal`` has no direct
# equivalent; low is the closest documented level.
_EFFORT_MAP = {
    "minimal": "low",
}


class AnthropicAdapter(ModelAdapter):
    provider = "anthropic"
    max_tokens_field = "max_tokens"

    # ------------------------------------------------------------------ wire
    def endpoint(self, base_url: str) -> str:
        base = (base_url or "").rstrip("/")
        if base.endswith("/v1/messages") or base.endswith("/messages"):
            return base
        if base.endswith("/v1"):
            return f"{base}/messages"
        return f"{base}/v1/messages"

    def headers(self, api_key: str) -> dict:
        # `cache_control` is a request-body field, not a header; sending it here
        # would be ignored at best and rejected by a strict proxy at worst.
        headers = {
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    def build_payload(
        self,
        messages: list,
        model: str,
        tools: list | None = None,
        use_tools: bool = True,
        options: LLMOptions | None = None,
    ) -> dict:
        options = options or LLMOptions()
        system, converted = self._convert_messages(messages)

        payload: dict = {
            "model": model,
            "messages": converted,
            # Required by the Messages API.  Thinking tokens count toward it.
            "max_tokens": options.max_tokens or DEFAULT_MAX_TOKENS,
            "stream": True,
        }
        if system:
            payload["system"] = system

        if use_tools and tools:
            payload["tools"] = self._convert_tools(tools)
            payload["tool_choice"] = {"type": "auto"}

        effort = normalize_effort(options.reasoning_effort)
        if effort:
            if effort == "none":
                payload["thinking"] = {"type": "disabled"}
            else:
                # Adaptive thinking + summarized display is the current shape;
                # budget_tokens is deprecated on current models.
                payload["thinking"] = {"type": "adaptive", "display": "summarized"}
                payload["output_config"] = {"effort": _EFFORT_MAP.get(effort, effort)}

        if options.temperature is not None:
            payload["temperature"] = options.temperature
        if options.top_p is not None:
            payload["top_p"] = options.top_p
        if options.extra:
            payload.update(options.extra)
        if options.prompt_cache is not False:
            self._apply_prompt_cache(payload, options)
        return payload

    # ---------------------------------------------------------------- caching
    @staticmethod
    def _cache_marker(options: LLMOptions) -> dict:
        """The `cache_control` value shared by every breakpoint in a request."""
        marker: dict = {"type": "ephemeral"}
        ttl = str(options.prompt_cache_ttl or "").strip().lower()
        if ttl in ("", "5m"):
            return marker
        if ttl == "1h":
            marker["ttl"] = "1h"
            return marker
        logger.warning(
            "Unknown prompt cache TTL %r; using the default 5 minutes",
            options.prompt_cache_ttl,
        )
        return marker

    def _apply_prompt_cache(self, payload: dict, options: LLMOptions) -> None:
        """Mark the prefixes a follow-up turn can reuse.

        The API caches everything from the start of the prompt up to each
        ``cache_control`` marker, in ``tools`` -> ``system`` -> ``messages``
        order.  Marking the last tool definition, the system prompt and the
        newest message therefore covers the three prefixes an agent turn
        repeats.  The message marker moves forward on its own because every
        request marks its own last block, so the previous turn is read from
        cache and only the new turn is written.

        Prompts below the model's minimum cacheable length are processed
        without caching; the API does not return an error, so no client-side
        token estimate is attempted here.
        """
        if "cache_control" in payload:
            # The caller manages caching itself: a top-level marker enables
            # automatic caching, and `None` disables it.  Either way our
            # block-level markers must not interfere.
            return

        marker = self._cache_marker(options)

        # Three breakpoints, one below the API's limit of four: tools, system
        # and messages (in cache-prefix order).
        tools = payload.get("tools")
        if tools:
            tools[-1] = {**tools[-1], "cache_control": dict(marker)}

        system = payload.get("system")
        if isinstance(system, str):
            payload["system"] = [
                {"type": "text", "text": system, "cache_control": dict(marker)}
            ]
        elif isinstance(system, list) and system:
            last = system[-1]
            if isinstance(last, dict) and last.get("type") in CACHEABLE_BLOCK_TYPES:
                system[-1] = {**last, "cache_control": dict(marker)}

        messages = payload.get("messages") or []
        if messages:
            self._mark_message(messages[-1], marker)

    @staticmethod
    def _mark_message(message: dict, marker: dict) -> bool:
        """Put a breakpoint on the last cacheable block of ``message``."""
        content = message.get("content")
        if isinstance(content, str):
            if not content:
                return False
            message["content"] = [
                {"type": "text", "text": content, "cache_control": dict(marker)}
            ]
            return True
        if not isinstance(content, list):
            return False

        for block in reversed(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") not in CACHEABLE_BLOCK_TYPES:
                continue
            if block.get("type") == "text" and not block.get("text"):
                continue
            block["cache_control"] = dict(marker)
            return True
        return False

    def _convert_tools(self, tools: list) -> list:
        converted = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") != "function":
                continue
            fn = tool.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            schema = fn.get("parameters") or {"type": "object", "properties": {}}
            converted.append(
                {
                    "name": name,
                    "description": fn.get("description", ""),
                    "input_schema": schema,
                }
            )
        return converted

    def _convert_messages(self, messages: list) -> tuple[str, list]:
        system_parts: list[str] = []
        converted: list[dict] = []
        pending_tool_results: list[dict] = []

        def flush_tool_results() -> None:
            if pending_tool_results:
                converted.append(
                    {"role": "user", "content": list(pending_tool_results)}
                )
                pending_tool_results.clear()

        for message in messages or []:
            role = message.get("role")
            if role == "system":
                flush_tool_results()
                text = self._content_to_text(message.get("content"))
                if text:
                    system_parts.append(text)
                continue

            if role == "user":
                flush_tool_results()
                blocks = self._to_anthropic_blocks(message.get("content"))
                if blocks:
                    converted.append({"role": "user", "content": blocks})
                continue

            if role == "assistant":
                flush_tool_results()
                blocks = []
                # Signed thinking blocks must be passed back unchanged when a
                # turn contains tool calls.
                for block in message.get("reasoning_blocks") or []:
                    if isinstance(block, dict) and block.get("type") in (
                        "thinking",
                        "redacted_thinking",
                    ):
                        blocks.append(dict(block))

                blocks.extend(self._to_anthropic_blocks(message.get("content")))

                for index, tc in enumerate(message.get("tool_calls") or []):
                    fn = tc.get("function") or {}
                    name = fn.get("name")
                    if not name:
                        continue
                    raw_args = fn.get("arguments")
                    try:
                        args = (
                            json.loads(raw_args)
                            if isinstance(raw_args, str) and raw_args.strip()
                            else (raw_args or {})
                        )
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id") or f"toolu_{index}",
                            "name": name,
                            "input": args,
                        }
                    )

                if blocks:
                    converted.append({"role": "assistant", "content": blocks})
                continue

            if role == "tool":
                tool_call_id = message.get("tool_call_id")
                if not tool_call_id:
                    continue
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": self._tool_result_content(message.get("content")),
                    }
                )

        flush_tool_results()
        return "\n\n".join(system_parts), converted

    @staticmethod
    def _content_to_text(content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return str(content)

    def _to_anthropic_blocks(self, content) -> list:
        if content is None:
            return []
        if isinstance(content, str):
            return [{"type": "text", "text": content}] if content else []
        if not isinstance(content, list):
            return [{"type": "text", "text": str(content)}]

        blocks = []
        for part in content:
            if isinstance(part, str):
                if part:
                    blocks.append({"type": "text", "text": part})
                continue
            if not isinstance(part, dict):
                continue

            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text", "")
                if text:
                    blocks.append({"type": "text", "text": text})
            elif part_type == "image_url":
                image = part.get("image_url") or {}
                url = image.get("url") if isinstance(image, dict) else image
                block = self._image_block(url)
                if block:
                    blocks.append(block)
            elif part_type in ("image", "tool_result", "tool_use", "thinking"):
                blocks.append(dict(part))
            else:
                # Unknown content parts are dropped rather than sent to a
                # provider that would reject them.
                continue
        return blocks

    @staticmethod
    def _image_block(url) -> dict | None:
        if not isinstance(url, str) or not url:
            return None
        if url.startswith("data:"):
            # data:<media-type>;base64,<payload>
            try:
                header, data = url.split(",", 1)
                media_type = header[5:].split(";", 1)[0]
            except ValueError:
                return None
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            }
        return {"type": "image", "source": {"type": "url", "url": url}}

    def _tool_result_content(self, content):
        if isinstance(content, str):
            return content
        blocks = self._to_anthropic_blocks(content)
        return blocks or ""

    # --------------------------------------------------------------- stream
    def stream_and_collect(self, response, sink: StreamSink | None = None) -> dict:
        sink = sink or PrintSink()
        sink.start()

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_blocks: list[dict] = []
        tool_calls: list[dict] = []
        block_states: dict[int, dict] = {}
        role = "assistant"
        stop_reason = None
        usage: dict = {}

        try:
            for event, data in iter_sse(response):
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue

                event_type = obj.get("type") or event

                if event_type == "error":
                    raise stream_error(obj, provider=self.provider)

                if event_type == "message_start":
                    message = obj.get("message") or {}
                    role = message.get("role", role)
                    usage.update(message.get("usage") or {})
                    continue

                if event_type == "content_block_start":
                    index = obj.get("index", 0)
                    block = obj.get("content_block") or {}
                    block_type = block.get("type")
                    state = {"type": block_type}

                    if block_type == "text":
                        state["text"] = block.get("text", "")
                        if state["text"]:
                            content_parts.append(state["text"])
                            sink.content(state["text"])
                    elif block_type == "thinking":
                        state["thinking"] = block.get("thinking", "")
                        state["signature"] = block.get("signature", "")
                        if state["thinking"]:
                            reasoning_parts.append(state["thinking"])
                            sink.reasoning(state["thinking"])
                    elif block_type == "redacted_thinking":
                        state["data"] = block.get("data", "")
                    elif block_type == "tool_use":
                        state["id"] = block.get("id")
                        state["name"] = block.get("name", "")
                        state["json"] = ""
                        state["input"] = block.get("input") or {}
                    block_states[index] = state
                    continue

                if event_type == "content_block_delta":
                    index = obj.get("index", 0)
                    state = block_states.setdefault(index, {})
                    delta = obj.get("delta") or {}
                    delta_type = delta.get("type")

                    if delta_type == "text_delta":
                        text = delta.get("text", "")
                        state["text"] = state.get("text", "") + text
                        if text:
                            content_parts.append(text)
                            sink.content(text)
                    elif delta_type == "thinking_delta":
                        text = delta.get("thinking", "")
                        state["thinking"] = state.get("thinking", "") + text
                        if text:
                            reasoning_parts.append(text)
                            sink.reasoning(text)
                    elif delta_type == "signature_delta":
                        state["signature"] = state.get("signature", "") + delta.get(
                            "signature", ""
                        )
                    elif delta_type == "input_json_delta":
                        state["json"] = state.get("json", "") + delta.get(
                            "partial_json", ""
                        )
                    continue

                if event_type == "content_block_stop":
                    index = obj.get("index", 0)
                    state = block_states.pop(index, None)
                    if not state:
                        continue
                    block_type = state.get("type")
                    if block_type == "tool_use":
                        raw_json = state.get("json", "")
                        try:
                            tool_input = (
                                json.loads(raw_json)
                                if raw_json.strip()
                                else state.get("input", {})
                            )
                        except json.JSONDecodeError:
                            tool_input = state.get("input", {})
                        if not isinstance(tool_input, dict):
                            tool_input = {}
                        tool_calls.append(
                            {
                                "id": state.get("id") or f"toolu_{index}",
                                "type": "function",
                                "function": {
                                    "name": state.get("name", ""),
                                    "arguments": json.dumps(
                                        tool_input, ensure_ascii=False
                                    ),
                                },
                            }
                        )
                    elif block_type in ("thinking", "redacted_thinking"):
                        block = {"type": block_type}
                        if block_type == "thinking":
                            block["thinking"] = state.get("thinking", "")
                            if state.get("signature"):
                                block["signature"] = state["signature"]
                        else:
                            block["data"] = state.get("data", "")
                        reasoning_blocks.append(block)
                    continue

                if event_type == "message_delta":
                    delta = obj.get("delta") or {}
                    stop_reason = delta.get("stop_reason") or stop_reason
                    # Output tokens arrive here; input and cache counters were
                    # already sent in message_start.
                    usage.update(obj.get("usage") or {})
                    continue

                if event_type == "message_stop":
                    break
        finally:
            sink.end()

        message: dict = {"role": role, "content": "".join(content_parts)}
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        if reasoning_blocks:
            message["reasoning_blocks"] = reasoning_blocks
        if tool_calls:
            message["tool_calls"] = tool_calls
        if stop_reason:
            message["finish_reason"] = stop_reason
        if usage:
            message["usage"] = usage
            logger.debug(
                "Prompt cache: %s read, %s written, %s uncached input tokens",
                usage.get("cache_read_input_tokens", 0),
                usage.get("cache_creation_input_tokens", 0),
                usage.get("input_tokens", 0),
            )
        return message
