import json

import pytest

from cli.sink import BufferSink
from db.session_db import SessionDB
from utils.console import safe_write
from utils.llm import (
    AnthropicAdapter,
    DeepSeekAdapter,
    LLMOptions,
    ModelAdapter,
    OpenAIAdapter,
    OpenRouterAdapter,
    get_adapter,
)
from utils.llm.base import iter_sse
from utils.llm_client import call_llm_streaming


class _FakeResponse:
    def __init__(self, lines):
        self._lines = list(lines)
        self.ok = True
        self.status_code = 200
        self.headers = {}
        self.text = ""

    def iter_lines(self):
        return iter(self._lines)

    def raise_for_status(self):
        return None


def _sse(*objs, event=None):
    lines = []
    for obj in objs:
        if event:
            lines.append(f"event: {event}".encode("utf-8"))
        lines.append(f"data: {json.dumps(obj)}".encode("utf-8"))
        lines.append(b"")
    return lines


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look something up",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        },
    }
]


def _history():
    return [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": "answer",
            "reasoning_content": "thinking",
            "reasoning_details": [{"type": "reasoning.text", "text": "thinking"}],
            "reasoning_blocks": [
                {"type": "thinking", "thinking": "thinking", "signature": "sig"}
            ],
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q": "x"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "result"},
    ]


@pytest.mark.unit
class TestRegistry:
    def test_explicit_provider_wins(self):
        assert isinstance(get_adapter(provider_name="anthropic"), AnthropicAdapter)
        assert isinstance(get_adapter(provider_name="openai"), OpenAIAdapter)
        assert isinstance(get_adapter(provider_name="deepseek"), DeepSeekAdapter)
        assert isinstance(get_adapter(provider_name="openrouter"), OpenRouterAdapter)

    def test_base_url_inference(self):
        assert isinstance(
            get_adapter(api_base_url="https://api.anthropic.com"), AnthropicAdapter
        )
        assert isinstance(
            get_adapter(api_base_url="https://openrouter.ai/api/v1"),
            OpenRouterAdapter,
        )
        assert isinstance(
            get_adapter(api_base_url="https://api.openai.com/v1"), OpenAIAdapter
        )
        assert isinstance(
            get_adapter(api_base_url="https://api.deepseek.com"), DeepSeekAdapter
        )

    def test_generic_openai_compatible_fallback(self):
        assert type(get_adapter("local", api_base_url="http://localhost:8080")) is (
            ModelAdapter
        )

    def test_explicit_openai_compatible_opts_out_of_inference(self):
        adapter = get_adapter(
            "gpt-oss-120b",
            provider_name="openai_compatible",
            api_base_url="https://api.openai.com/v1",
        )
        assert type(adapter) is ModelAdapter


@pytest.mark.unit
class TestPayloads:
    def test_openai_uses_current_token_and_reasoning_fields(self):
        payload = OpenAIAdapter().build_payload(
            _history(),
            "gpt-6-astra",
            tools=TOOLS,
            options=LLMOptions(max_tokens=512, reasoning_effort="high"),
        )

        assert payload["max_completion_tokens"] == 512
        assert "max_tokens" not in payload
        assert payload["reasoning_effort"] == "high"
        # No llama.cpp-only parameters leak into a remote request.
        for legacy in (
            "cache_prompt",
            "reasoning_format",
            "repeat_last_n",
            "repeat_penalty",
            "timings_per_token",
            "top_k",
            "top_n_sigma",
            "typical_p",
        ):
            assert legacy not in payload
        # Reasoning metadata is not valid OpenAI chat history.
        assistant = payload["messages"][2]
        assert "reasoning_content" not in assistant
        assert "reasoning_details" not in assistant
        assert "reasoning_blocks" not in assistant
        # Newer OpenAI models use developer messages for instructions.
        assert payload["messages"][0]["role"] == "developer"

    def test_deepseek_keeps_reasoning_only_with_tools(self):
        with_tools = DeepSeekAdapter().build_payload(
            _history(), "deepseek-flash", tools=TOOLS
        )
        without_tools = DeepSeekAdapter().build_payload(
            _history(), "deepseek-flash", use_tools=False
        )

        assert with_tools["messages"][2]["reasoning_content"] == "thinking"
        assert "reasoning_content" not in without_tools["messages"][2]

    def test_deepseek_effort_mapping_and_disable(self):
        adapter = DeepSeekAdapter()
        payload = adapter.build_payload(
            _history(), "deepseek-flash", options=LLMOptions(reasoning_effort="medium")
        )
        assert payload["reasoning_effort"] == "high"

        disabled = adapter.build_payload(
            _history(), "deepseek-flash", options=LLMOptions(reasoning_effort="none")
        )
        assert disabled["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in disabled

    def test_openrouter_uses_reasoning_object_and_details(self):
        payload = OpenRouterAdapter().build_payload(
            _history(),
            "deepseek/deepseek-v4-pro",
            tools=TOOLS,
            options=LLMOptions(max_tokens=256, reasoning_effort="low"),
        )

        assert payload["max_tokens"] == 256
        assert payload["reasoning"] == {"effort": "low"}
        assistant = payload["messages"][2]
        assert assistant["reasoning_details"] == [
            {"type": "reasoning.text", "text": "thinking"}
        ]
        assert "reasoning_content" not in assistant

    def test_anthropic_converts_protocol_and_groups_tool_results(self):
        adapter = AnthropicAdapter()
        payload = adapter.build_payload(
            _history(),
            "claude-opus-5-5",
            tools=TOOLS,
            options=LLMOptions(max_tokens=1024, reasoning_effort="high"),
        )

        assert payload["system"] == "system prompt"
        assert payload["max_tokens"] == 1024
        assert payload["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert payload["output_config"] == {"effort": "high"}
        assert payload["tools"] == [
            {
                "name": "lookup",
                "description": "Look something up",
                "input_schema": TOOLS[0]["function"]["parameters"],
            }
        ]
        # System is top-level, not a message.
        assert all(message["role"] != "system" for message in payload["messages"])
        # Thinking block with its signature precedes the text and tool_use.
        assistant = payload["messages"][1]
        assert assistant["content"][0] == {
            "type": "thinking",
            "thinking": "thinking",
            "signature": "sig",
        }
        assert assistant["content"][1] == {"type": "text", "text": "answer"}
        assert assistant["content"][2]["type"] == "tool_use"
        assert assistant["content"][2]["input"] == {"q": "x"}
        # Tool results are packed into a single user message.
        assert payload["messages"][2]["role"] == "user"
        assert payload["messages"][2]["content"] == [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "result"}
        ]

    def test_anthropic_requires_max_tokens_and_headers(self):
        adapter = AnthropicAdapter()
        payload = adapter.build_payload([{"role": "user", "content": "hi"}], "claude")
        assert payload["max_tokens"] == 8192
        headers = adapter.headers("secret")
        assert headers["x-api-key"] == "secret"
        assert headers["anthropic-version"] == "2023-06-01"
        assert "Authorization" not in headers

    def test_endpoint_normalisation(self):
        assert (
            OpenAIAdapter().endpoint("https://api.openai.com/v1")
            == "https://api.openai.com/v1/chat/completions"
        )
        assert (
            DeepSeekAdapter().endpoint("https://api.deepseek.com")
            == "https://api.deepseek.com/chat/completions"
        )
        assert (
            OpenRouterAdapter().endpoint("https://openrouter.ai/api/v1")
            == "https://openrouter.ai/api/v1/chat/completions"
        )
        assert (
            AnthropicAdapter().endpoint("https://api.anthropic.com")
            == "https://api.anthropic.com/v1/messages"
        )


@pytest.mark.unit
class TestCallLlmStreaming:
    def test_adapter_controls_url_headers_and_payload(self, monkeypatch):
        captured = {}

        class _Session:
            def post(self, url, **kwargs):
                captured["url"] = url
                captured.update(kwargs)
                return _FakeResponse(
                    _sse(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        },
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "Hi"},
                        },
                        {"type": "message_stop"},
                    )
                )

        monkeypatch.setattr(
            "utils.llm_client.get_request_session",
            lambda api_key="": _Session(),
        )

        result = call_llm_streaming(
            [{"role": "user", "content": "hello"}],
            model="claude-opus-5-5",
            llm_base="https://api.anthropic.com",
            api_key="secret",
            adapter=AnthropicAdapter(),
            max_tokens=256,
            reasoning_effort="medium",
        )

        assert captured["url"] == "https://api.anthropic.com/v1/messages"
        assert captured["headers"]["x-api-key"] == "secret"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"
        assert "Authorization" not in captured["headers"]
        assert captured["json"]["max_tokens"] == 256
        assert captured["json"]["output_config"] == {"effort": "medium"}
        assert result["content"] == "Hi"


@pytest.mark.unit
class TestStreamDecoders:
    def test_openai_decoder_handles_reasoning_and_tool_calls(self):
        response = _FakeResponse(
            _sse(
                {"choices": [{"delta": {"reasoning": "think "}}]},
                {"choices": [{"delta": {"content": "Hi"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "lookup",
                                            "arguments": '{"q":',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": '"x"}'}}
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            )
        )
        sink = BufferSink()
        message = ModelAdapter().stream_and_collect(response, sink)

        assert message["content"] == "Hi"
        assert message["reasoning_content"] == "think "
        assert message["tool_calls"][0]["function"]["arguments"] == '{"q":"x"}'
        assert message["finish_reason"] == "tool_calls"
        assert sink.reasoning_text == "think "
        assert sink.content_text == "Hi"

    def test_anthropic_decoder_handles_thinking_and_partial_tool_json(self):
        events = [
            (
                "message_start",
                {"type": "message_start", "message": {"role": "assistant"}},
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "thinking", "thinking": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "why"},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "signature_delta", "signature": "sig"},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "Hello"},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 2,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "lookup",
                        "input": {},
                    },
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 2,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"q": "x"',
                    },
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 2,
                    "delta": {"type": "input_json_delta", "partial_json": "}"},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 2}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        lines = []
        for event, obj in events:
            lines.append(f"event: {event}".encode("utf-8"))
            lines.append(f"data: {json.dumps(obj)}".encode("utf-8"))
            lines.append(b"")

        sink = BufferSink()
        message = AnthropicAdapter().stream_and_collect(_FakeResponse(lines), sink)

        assert message["content"] == "Hello"
        assert message["reasoning_content"] == "why"
        assert message["reasoning_blocks"] == [
            {"type": "thinking", "thinking": "why", "signature": "sig"}
        ]
        assert message["tool_calls"][0]["function"]["arguments"] == '{"q": "x"}'
        assert message["finish_reason"] == "tool_use"
        assert sink.reasoning_text == "why"
        assert sink.content_text == "Hello"

    def test_sse_parser_handles_keepalive_and_blank_separators(self):
        response = _FakeResponse(
            [
                b": OPENROUTER PROCESSING",
                b"event: message_start",
                b'data: {"type": "message_start"}',
                b"",
                b'data: {"choices": []}',
            ]
        )
        events = list(iter_sse(response))
        assert events == [
            ("message_start", '{"type": "message_start"}'),
            (None, '{"choices": []}'),
        ]


@pytest.mark.unit
class TestReasoningPersistence:
    def test_reasoning_survives_a_db_round_trip(self, tmp_path):
        db = SessionDB(str(tmp_path / "sessions.db"))
        session_id = db.create_session("reasoning")
        db.add_message(
            session_id,
            "assistant",
            "answer",
            reasoning_content="thinking",
            reasoning_details=[{"type": "reasoning.text", "text": "thinking"}],
            reasoning_blocks=[
                {"type": "thinking", "thinking": "thinking", "signature": "sig"}
            ],
        )

        message = db.get_messages(session_id)[0]
        assert message["reasoning_content"] == "thinking"
        assert message["reasoning_details"] == [
            {"type": "reasoning.text", "text": "thinking"}
        ]
        assert message["reasoning_blocks"] == [
            {"type": "thinking", "thinking": "thinking", "signature": "sig"}
        ]


@pytest.mark.unit
class TestSafeConsole:
    def test_safe_write_escapes_unencodable_characters(self):
        class AsciiOnly:
            encoding = "ascii"

            def __init__(self):
                self.data = ""

            def write(self, text):
                text.encode("ascii")
                self.data += text

            def flush(self):
                pass

        stream = AsciiOnly()
        safe_write("中文", stream)

        assert "\\u4e2d" in stream.data
        assert "\\u6587" in stream.data
