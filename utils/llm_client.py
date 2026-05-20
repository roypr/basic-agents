import json
import logging
from typing import Optional
import requests
from .http_utils import get_request_session

logger = logging.getLogger("basic_agents")


class ModelAdapter:
    """Base adapter for OpenAI-compatible responses."""

    def extract_content(self, message: dict) -> str:
        return message.get("content") or ""

    def extract_reasoning(self, message: dict) -> str:
        return message.get("reasoning_content") or ""

    def extract_tool_calls(self, message: dict) -> list:
        return message.get("tool_calls") or []

    def stream_and_collect(self, response: requests.Response) -> dict:
        collected_content = []
        collected_reasoning = []
        tool_calls_acc: dict[int, dict] = {}
        role = "assistant"

        print("\n", end="", flush=True)

        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if line.startswith("data:"):
                line = line[5:].strip()
            if line in ("", "[DONE]"):
                continue

            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue

            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta", {})

            if delta.get("role"):
                role = delta["role"]

            reasoning_delta = self._reasoning_delta(delta)
            if reasoning_delta:
                if not collected_reasoning:
                    print("[Think] ", end="", flush=True)
                collected_reasoning.append(reasoning_delta)
                print(reasoning_delta, end="", flush=True)

            content_delta = delta.get("content") or ""
            if content_delta:
                if collected_reasoning and not collected_content:
                    print()
                collected_content.append(content_delta)
                print(content_delta, end="", flush=True)

            for tc_delta in delta.get("tool_calls") or []:
                idx = tc_delta.get("index", 0)
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                acc = tool_calls_acc[idx]
                if tc_delta.get("id"):
                    acc["id"] += tc_delta["id"]
                fn = tc_delta.get("function") or {}
                if fn.get("name"):
                    acc["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    acc["function"]["arguments"] += fn["arguments"]

        print()

        message = {"role": role, "content": "".join(collected_content)}
        if collected_reasoning:
            message["reasoning_content"] = "".join(collected_reasoning)
        if tool_calls_acc:
            message["tool_calls"] = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]

        return message

    def _reasoning_delta(self, delta: dict) -> str:
        return delta.get("reasoning_content") or ""


class QwenAdapter(ModelAdapter):
    def _reasoning_delta(self, delta: dict) -> str:
        return delta.get("reasoning_content") or ""


class GemmaAdapter(ModelAdapter):
    def _reasoning_delta(self, delta: dict) -> str:
        return ""


class DeepSeekAdapter(ModelAdapter):
    def _reasoning_delta(self, delta: dict) -> str:
        return delta.get("reasoning_content") or ""

    def build_assistant_message(self, content: str, reasoning: str, tool_calls: list) -> dict:
        msg = {"role": "assistant", "content": content}
        if reasoning:
            msg["reasoning_content"] = reasoning
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg


MODEL_ADAPTERS = {
    "qwen": QwenAdapter,
    "gemma": GemmaAdapter,
    "deepseek": DeepSeekAdapter,
}


def get_adapter(model_name: str) -> ModelAdapter:
    key = model_name.lower()
    for fragment, cls in MODEL_ADAPTERS.items():
        if fragment in key:
            return cls()
    return ModelAdapter()


def call_llm_streaming(messages: list, model: str, llm_base: str, api_key: str,
                       adapter: ModelAdapter, use_tools: bool = True,
                       tools: list | None = None) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "cache_prompt": True,
        "reasoning_format": "deepseek",
        "repeat_last_n": 256,
        "repeat_penalty": 1.15,
        "temperature": 0.2,
        "timings_per_token": True,
        "top_k": 20,
        "top_n_sigma": 0,
        "top_p": 0.9,
        "typical_p": 1,
    }

    if use_tools and tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    logger.debug("LLM Payload:\n%s", json.dumps(payload, indent=2))

    response = get_request_session(api_key=api_key).post(
        f"{llm_base}/chat/completions",
        json=payload,
        stream=True,
        timeout=1200,
    )
    logger.debug("Status: %s", response.status_code)
    logger.debug("Response headers: %s", dict(response.headers))
    if not response.ok:
        logger.error("Raw response body: %s", response.text)
    response.raise_for_status()
    return adapter.stream_and_collect(response)
