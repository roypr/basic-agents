"""OpenAI, DeepSeek and OpenRouter adapters.

``openai`` speaks the Responses API (``POST /v1/responses``): its input items
and typed event stream are translated to/from the canonical message dialect
here.  DeepSeek and OpenRouter still speak Chat Completions and differ in the
names and semantics of their request fields; keeping those differences explicit
avoids the old behaviour of sending llama.cpp-specific parameters to every
remote provider.
"""

from __future__ import annotations

import json

from .base import (
    LLMOptions,
    ModelAdapter,
    PrintSink,
    StreamSink,
    iter_sse,
    normalize_effort,
)
from .errors import is_error_payload, stream_error


class OpenAIAdapter(ModelAdapter):
    """Official OpenAI Responses API (``POST /v1/responses``).

    The Responses API is OpenAI's current surface for reasoning control
    (``reasoning.effort`` plus summarised reasoning); Chat Completions only
    exposes a subset of it.  Requests therefore use input items and the decoded
    stream is rebuilt into the same canonical assistant message the other
    adapters produce.
    """

    provider = "openai"
    # Responses API names the output budget ``max_output_tokens``.
    max_tokens_field = "max_output_tokens"

    # ------------------------------------------------------------------ wire
    def endpoint(self, base_url: str) -> str:
        base = (base_url or "").rstrip("/")
        if base.endswith("/responses"):
            return base
        if base.endswith("/chat/completions"):
            # Older providers.json files point at the Chat Completions path.
            return f"{base[: -len('/chat/completions')]}/responses"
        if base.endswith("/v1") or base.rsplit("/", 1)[-1].startswith("v"):
            return f"{base}/responses"
        return f"{base}/v1/responses"

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
            "input": self.normalize_messages(
                messages, use_tools=bool(use_tools and tools)
            ),
            "stream": True,
            # The conversation lives in our session store, not on OpenAI's
            # side, so reasoning items have to be replayed by hand.
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }

        if use_tools and tools:
            payload["tools"] = self._convert_tools(tools)
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

    def apply_reasoning(self, payload: dict, effort: str | None) -> None:
        effort = normalize_effort(effort)
        if not effort:
            return
        reasoning: dict = {"effort": effort}
        if effort != "none":
            # Without a summary the model reasons silently and nothing can be
            # rendered, so ask for one whenever reasoning is enabled.
            reasoning["summary"] = "auto"
        payload["reasoning"] = reasoning

    # ----------------------------------------------------------------- input
    def normalize_messages(self, messages: list, use_tools: bool = True) -> list:
        """Convert the canonical chat history into Responses input items.

        The Responses API is item based: assistant turns become
        ``message``/``function_call``/``reasoning`` items and tool results
        become ``function_call_output`` items.
        """
        items: list = []
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "tool":
                items.append(self._tool_output_item(message))
            elif role == "assistant":
                items.extend(self._assistant_items(message))
            else:
                items.append(self._message_item(role, message.get("content")))
        return items

    def _convert_tools(self, tools: list) -> list:
        converted = []
        for tool in tools or []:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                continue
            fn = tool.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            converted.append(
                {
                    "type": "function",
                    "name": name,
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        return converted

    def _message_item(self, role, content) -> dict:
        # With o-series and newer, developer messages replace system messages.
        item: dict = {"role": "developer" if role == "system" else (role or "user")}
        if isinstance(content, str):
            item["content"] = content
        else:
            item["content"] = self._input_parts(content)
        return item

    def _assistant_items(self, message: dict) -> list:
        items: list = []

        # Reasoning items must be replayed verbatim and in order ahead of the
        # output they produced; without ``encrypted_content`` they cannot be
        # referenced when the request is stateless, so those are dropped.
        for block in message.get("reasoning_blocks") or []:
            if not isinstance(block, dict) or block.get("type") != "reasoning":
                continue
            if not (block.get("id") and block.get("encrypted_content")):
                continue
            items.append(
                {
                    "type": "reasoning",
                    "id": block["id"],
                    "summary": block.get("summary") or [],
                    "encrypted_content": block["encrypted_content"],
                }
            )

        text = self._content_text(message.get("content"))
        if text:
            items.append({"role": "assistant", "content": text})

        for index, tool_call in enumerate(message.get("tool_calls") or []):
            if not isinstance(tool_call, dict):
                continue
            fn = tool_call.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            arguments = fn.get("arguments")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments or {}, ensure_ascii=False)
            items.append(
                {
                    "type": "function_call",
                    "call_id": tool_call.get("id") or f"call_{index}",
                    "name": name,
                    "arguments": arguments,
                }
            )
        return items

    def _tool_output_item(self, message: dict) -> dict:
        content = message.get("content")
        output = content if isinstance(content, str) else self._input_parts(content)
        return {
            "type": "function_call_output",
            "call_id": message.get("tool_call_id") or "",
            "output": output,
        }

    @staticmethod
    def _content_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return "" if content is None else str(content)

    @staticmethod
    def _input_parts(content) -> list:
        """Map canonical content parts onto Responses input content parts."""
        parts: list = []
        if isinstance(content, str):
            return [{"type": "input_text", "text": content}] if content else []
        for part in content or []:
            if isinstance(part, str):
                parts.append({"type": "input_text", "text": part})
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                parts.append({"type": "input_text", "text": part.get("text", "")})
            elif part_type == "image_url":
                image = part.get("image_url") or {}
                url = image.get("url") if isinstance(image, dict) else image
                if url:
                    parts.append({"type": "input_image", "image_url": url})
            # Unknown parts are dropped: the Responses API rejects them.
        return parts

    # --------------------------------------------------------------- stream
    def stream_and_collect(self, response, sink: StreamSink | None = None) -> dict:
        sink = sink or PrintSink()
        sink.start()

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_states: dict[int, dict] = {}
        tool_states: dict[int, dict] = {}
        status = None
        incomplete_reason = None
        finished = False

        try:
            for event_name, data in iter_sse(response):
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue

                event_type = event.get("type") or event_name or ""

                if event_type == "response.failed":
                    raise stream_error(
                        self._error_payload(event), provider=self.provider
                    )
                # A bare error envelope (a proxy in front of the endpoint may
                # send ``{"error": ...}`` without a ``type``).
                if is_error_payload(event):
                    raise stream_error(event, provider=self.provider)

                if event_type == "response.output_text.delta":
                    text = event.get("delta") or ""
                    if text:
                        content_parts.append(text)
                        sink.content(text)
                    continue

                if event_type in (
                    "response.reasoning_summary_text.delta",
                    "response.reasoning_text.delta",
                ):
                    text = event.get("delta") or ""
                    if text:
                        reasoning_parts.append(text)
                        sink.reasoning(text)
                    continue

                if event_type == "response.output_item.added":
                    item = event.get("item") or {}
                    index = event.get("output_index", 0)
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        self._track_tool_call(item, index, tool_states)
                    elif isinstance(item, dict) and item.get("type") == "reasoning":
                        reasoning_states[index] = self._reasoning_item(item)
                    continue

                if event_type == "response.function_call_arguments.delta":
                    state = tool_states.setdefault(
                        event.get("output_index", 0), {"arguments": ""}
                    )
                    state["arguments"] += event.get("delta") or ""
                    continue

                if event_type == "response.function_call_arguments.done":
                    state = tool_states.setdefault(
                        event.get("output_index", 0), {"arguments": ""}
                    )
                    if isinstance(event.get("arguments"), str):
                        state["arguments"] = event["arguments"]
                    continue

                if event_type == "response.output_item.done":
                    item = event.get("item") or {}
                    index = event.get("output_index", 0)
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        self._track_tool_call(item, index, tool_states, final=True)
                    elif isinstance(item, dict) and item.get("type") == "reasoning":
                        # Only the completed item carries usable encrypted
                        # content; the ``added`` copy may still be partial.
                        reasoning_states[index] = self._reasoning_item(item)
                    continue

                if event_type in ("response.completed", "response.incomplete"):
                    final = event.get("response") or {}
                    status = final.get("status") or event_type.rsplit(".", 1)[-1]
                    details = final.get("incomplete_details") or {}
                    incomplete_reason = (
                        details.get("reason") or incomplete_reason
                        if isinstance(details, dict)
                        else incomplete_reason
                    )
                    finished = True
                    break
                # response.created / in_progress / content_part / refusal ...
        finally:
            sink.end()

        blocks = [reasoning_states[i] for i in sorted(reasoning_states)]
        if not reasoning_parts and blocks:
            # No summary deltas (some deployments only send the final item):
            # fall back to the summary the reasoning item carries.
            reasoning_parts = [
                summary.get("text", "")
                for block in blocks
                for summary in block.get("summary") or []
                if isinstance(summary, dict)
            ]

        message: dict = {"role": "assistant", "content": "".join(content_parts)}
        reasoning = "".join(reasoning_parts)
        if reasoning:
            message["reasoning_content"] = reasoning
        if blocks:
            message["reasoning_blocks"] = blocks

        tool_calls = []
        for index in sorted(tool_states):
            state = tool_states[index]
            name = state.get("name")
            if not name:
                continue
            tool_calls.append(
                {
                    "id": state.get("call_id") or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": state.get("arguments") or "",
                    },
                }
            )
        if tool_calls:
            message["tool_calls"] = tool_calls

        message["finish_reason"] = self._finish_reason(
            bool(tool_calls), status, incomplete_reason, finished
        )
        return message

    @staticmethod
    def _finish_reason(
        has_tool_calls: bool,
        status: str | None,
        incomplete_reason: str | None,
        finished: bool,
    ) -> str:
        if has_tool_calls:
            return "tool_calls"
        if incomplete_reason in ("max_output_tokens", "max_tokens"):
            return "length"
        if status == "incomplete":
            return "incomplete"
        return "stop" if finished else "stream_incomplete"

    @staticmethod
    def _track_tool_call(
        item: dict, index: int, states: dict, final: bool = False
    ) -> None:
        state = states.setdefault(index, {"arguments": ""})
        if item.get("call_id") or item.get("id"):
            state["call_id"] = item.get("call_id") or item.get("id")
        if item.get("name"):
            state["name"] = item["name"]
        arguments = item.get("arguments")
        if isinstance(arguments, str) and (arguments or final):
            state["arguments"] = arguments

    @staticmethod
    def _reasoning_item(item: dict) -> dict:
        """Keep only the fields accepted when replaying a reasoning item."""
        block: dict = {
            "type": "reasoning",
            "id": item.get("id") or "",
            "summary": item.get("summary") or [],
        }
        if item.get("encrypted_content"):
            block["encrypted_content"] = item["encrypted_content"]
        return block

    @staticmethod
    def _error_payload(event: dict) -> dict:
        response = event.get("response") or {}
        error = response.get("error") if isinstance(response, dict) else None
        if isinstance(error, dict):
            return error
        return response if isinstance(response, dict) and response else event


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
