import json

import pytest

from cli.sink import BufferSink, ReplSink
from utils.llm_client import ModelAdapter, PrintSink, call_llm_streaming


class _FakeResponse:
    """Minimal requests.Response stand-in yielding SSE lines."""

    def __init__(self, lines, ok=True):
        self._lines = list(lines)
        self.ok = ok
        self.status_code = 200
        self.headers = {}

    def iter_lines(self):
        return iter(self._lines)

    def raise_for_status(self):
        return None


def _sse(*deltas) -> list:
    return [
        f"data: {json.dumps({'choices': [{'delta': d}]})}".encode("utf-8")
        for d in deltas
    ]


@pytest.mark.unit
class TestPrintSink:
    def test_default_framing_matches_original(self, capsys):
        sink = PrintSink()
        sink.start()
        sink.reasoning("thinking")
        sink.content("answer")
        sink.end()

        assert capsys.readouterr().out == "\n[Think] thinking\nanswer\n"

    def test_content_only_has_no_think_prefix(self, capsys):
        sink = PrintSink()
        sink.start()
        sink.content("Hi")
        sink.end()

        assert capsys.readouterr().out == "\nHi\n"

    def test_state_resets_between_streams(self, capsys):
        sink = PrintSink()
        sink.start()
        sink.content("one")
        sink.end()
        sink.start()
        sink.reasoning("r")
        sink.end()

        # Second stream must re-emit the [Think] prefix (state was reset).
        assert capsys.readouterr().out == "\none\n\n[Think] r\n"


@pytest.mark.unit
class TestStreamAndCollect:
    def test_default_sink_prints_and_returns_message(self, capsys):
        response = _FakeResponse(_sse({"role": "assistant"}, {"content": "Hi"}))
        message = ModelAdapter().stream_and_collect(response)

        assert message == {"role": "assistant", "content": "Hi"}
        assert capsys.readouterr().out == "\nHi\n"

    def test_buffer_sink_captures_without_printing(self, capsys):
        response = _FakeResponse(_sse({"reasoning_content": "why"}, {"content": "Hi"}))
        sink = BufferSink()
        message = ModelAdapter().stream_and_collect(response, sink)

        assert sink.started and sink.ended
        assert sink.reasoning_text == "why"
        assert sink.content_text == "Hi"
        assert message["content"] == "Hi"
        assert message["reasoning_content"] == "why"
        assert capsys.readouterr().out == ""

    def test_tool_calls_are_accumulated(self):
        response = _FakeResponse(
            _sse(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "web_search", "arguments": "{}"},
                        }
                    ]
                }
            )
        )
        message = ModelAdapter().stream_and_collect(response, BufferSink())

        assert message["tool_calls"][0]["id"] == "call_1"
        assert message["tool_calls"][0]["function"]["name"] == "web_search"


@pytest.mark.unit
class TestReplSink:
    def test_separator_precedes_stream(self, capsys):
        sink = ReplSink(separator="--\n")
        sink.start()
        sink.content("Hi")
        sink.end()

        assert capsys.readouterr().out == "--\n\nHi\n"


@pytest.mark.unit
class TestCallLlmStreamingSink:
    def test_sink_is_forwarded_to_adapter(self, monkeypatch):
        response = _FakeResponse(_sse({"content": "Hi"}))
        monkeypatch.setattr(
            "utils.llm_client.get_request_session",
            lambda api_key="": type(
                "S", (), {"post": lambda self, *a, **k: response}
            )(),
        )

        sink = BufferSink()
        result = call_llm_streaming(
            [{"role": "user", "content": "hi"}],
            model="local",
            llm_base="http://localhost",
            api_key="",
            adapter=ModelAdapter(),
            sink=sink,
        )

        assert sink.content_text == "Hi"
        assert result["content"] == "Hi"
