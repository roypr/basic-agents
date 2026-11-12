"""Tests for provider error normalisation, logging and rendering.

A response body must never decide how the CLI behaves: every provider dialect
folds into ``LLMError`` with a category, a retryability flag and a hint, while
the raw body is kept for ``logs/app_errors.log``.
"""

import json
import logging

import pytest
import requests

from utils.llm import (
    AnthropicAdapter,
    ErrorKind,
    LLMError,
    ModelAdapter,
    ProviderStreamError,
    detect_api_error,
    normalize_api_error,
    normalize_network_error,
    render_error,
    retry_after_seconds,
)
from utils.llm_client import call_llm_streaming


class _FakeResponse:
    def __init__(self, lines=(), *, status=200, body="", headers=None, ok=None):
        self._lines = list(lines)
        self.status_code = status
        self.text = body
        self.headers = dict(headers or {})
        self.ok = status < 400 if ok is None else ok

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


def _drop_file_handlers(root):
    for handler in list(root.handlers):
        if getattr(handler, "_basic_agents_file", False):
            root.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def error_log_in_tmp(tmp_path):
    """Keep provider-error logs out of the repository during tests."""
    import utils.logging_setup as logging_setup

    root = logging.getLogger()
    _drop_file_handlers(root)
    original_path = logging_setup.LOG_PATH
    logging_setup.LOG_PATH = tmp_path / "app_errors.log"
    logging_setup._configured = False
    try:
        yield logging_setup.LOG_PATH
    finally:
        _drop_file_handlers(root)
        logging_setup.LOG_PATH = original_path
        logging_setup._configured = False


@pytest.mark.unit
class TestNormalize:
    def test_openai_invalid_request_names_the_param(self):
        body = {
            "error": {
                "message": "Function tools with reasoning_effort are not supported "
                "for gpt-6-luna in /v1/chat/completions.",
                "type": "invalid_request_error",
                "param": "reasoning_effort",
                "code": None,
            }
        }
        error = normalize_api_error(
            json.dumps(body),
            provider="openai",
            model="gpt-6-luna",
            status_code=400,
            url="https://api.openai.com/v1/chat/completions",
        )

        assert error.kind == ErrorKind.INVALID_REQUEST
        assert error.status_code == 400
        assert error.param == "reasoning_effort"
        assert error.provider == "openai"
        assert error.model == "gpt-6-luna"
        assert not error.retryable
        # The hint is the whole point of normalising: it says what to do.
        assert "--reasoning-effort none" in error.hint

    def test_anthropic_authentication_error(self):
        body = {
            "type": "error",
            "error": {"type": "authentication_error", "message": "invalid x-api-key"},
        }
        error = normalize_api_error(body, provider="anthropic", status_code=401)

        assert error.kind == ErrorKind.AUTH
        assert error.error_type == "authentication_error"
        assert "api_key" in error.hint

    def test_plain_text_proxy_body_is_kept(self):
        error = normalize_api_error(
            "Authentication Fails (governor)", provider="custom", status_code=401
        )

        assert error.kind == ErrorKind.AUTH
        assert "Authentication Fails (governor)" in str(error)

    def test_status_is_recovered_from_the_envelope(self):
        error = normalize_api_error(
            '{"error":{"message":"No cookie auth credentials found","code":401}}',
            provider="local",
        )

        assert error.status_code == 401
        assert error.kind == ErrorKind.AUTH

    def test_html_error_page_is_flattened(self):
        body = "<html><body><h1>Bad Gateway</h1><p>upstream down</p></body></html>"
        error = normalize_api_error(body, status_code=502, provider="proxy")

        assert error.kind == ErrorKind.SERVER
        assert "<" not in str(error)
        assert "Bad Gateway" in str(error)
        assert error.retryable

    def test_empty_body_still_produces_a_message(self):
        error = normalize_api_error("", status_code=500, provider="local")

        assert error.kind == ErrorKind.SERVER
        assert "500" in str(error)

    def test_context_length_has_the_compression_hint(self):
        error = normalize_api_error(
            '{"error": {"message": "This model\'s maximum context length is 8192 tokens"}}',
            status_code=400,
            provider="openai",
        )

        assert error.kind == ErrorKind.CONTEXT_LENGTH
        assert "compress" in error.hint

    def test_rate_limit_carries_retry_after(self):
        error = normalize_api_error(
            '{"error": {"message": "Rate limit reached"}}',
            provider="openai",
            status_code=429,
            retry_after=retry_after_seconds({"Retry-After": "12"}),
        )

        assert error.kind == ErrorKind.RATE_LIMIT
        assert error.retryable
        assert "12s" in error.hint

    def test_successful_json_is_not_an_error(self):
        assert detect_api_error('{"choices": [{"message": {"content": "hi"}}]}') is None

    def test_network_error_categories(self):
        timeout = normalize_network_error(
            requests.Timeout("read timed out"), provider="openai", url="https://x/y"
        )
        refused = normalize_network_error(
            requests.ConnectionError("refused"), provider="openai", url="http://local"
        )

        assert timeout.kind == ErrorKind.TIMEOUT
        assert refused.kind == ErrorKind.NETWORK
        assert "local" in refused.hint

    def test_retry_after_parsing(self):
        assert retry_after_seconds({"Retry-After": "5"}) == 5
        assert retry_after_seconds({"retry-after": "1.5"}) == 1.5
        assert retry_after_seconds({}) is None
        assert retry_after_seconds({"Retry-After": "Wed, 21 Oct 2015"}) is None


@pytest.mark.unit
class TestCallLlmStreamingErrors:
    def _call(self, monkeypatch, response, **kwargs):
        class _Session:
            def post(self, url, **post_kwargs):
                if isinstance(response, Exception):
                    raise response
                return response

        monkeypatch.setattr(
            "utils.llm_client.get_request_session", lambda api_key="": _Session()
        )
        return call_llm_streaming(
            [{"role": "user", "content": "hello"}],
            model=kwargs.pop("model", "gpt-6-luna"),
            llm_base=kwargs.pop("llm_base", "https://api.openai.com/v1"),
            api_key="secret",
            **kwargs,
        )

    def test_non_ok_status_raises_normalised_error(self, monkeypatch, error_log_in_tmp):
        response = _FakeResponse(
            status=401, body='{"error": {"message": "Missing Authentication header"}}'
        )

        with pytest.raises(LLMError) as excinfo:
            self._call(monkeypatch, response)

        error = excinfo.value
        assert error.kind == ErrorKind.AUTH
        assert error.status_code == 401
        assert error.provider == "openai"
        assert error.model == "gpt-6-luna"
        assert "Missing Authentication header" in str(error)
        # The raw wire body is preserved for the log, not dumped at the user.
        assert "Raw response body" in error_log_in_tmp.read_text(encoding="utf-8")

    def test_transport_error_is_normalised(self, monkeypatch):
        with pytest.raises(LLMError) as excinfo:
            self._call(monkeypatch, requests.ConnectionError("connection refused"))

        assert excinfo.value.kind == ErrorKind.NETWORK
        assert excinfo.value.retryable

    def test_json_error_envelope_with_http_200(self, monkeypatch):
        response = _FakeResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body='{"error": {"message": "No cookie auth credentials found", "code": 401}}',
        )

        with pytest.raises(LLMError) as excinfo:
            self._call(monkeypatch, response)

        assert excinfo.value.kind == ErrorKind.AUTH
        assert excinfo.value.status_code == 401

    def test_empty_json_body_with_http_200(self, monkeypatch):
        response = _FakeResponse(
            status=200, headers={"Content-Type": "application/json"}, body=""
        )

        with pytest.raises(LLMError) as excinfo:
            self._call(monkeypatch, response)

        assert excinfo.value.kind == ErrorKind.EMPTY_RESPONSE

    def test_stream_error_chunk_raises_provider_stream_error(self, monkeypatch):
        response = _FakeResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            lines=_sse(
                {"choices": [{"delta": {"content": "partial"}}]},
                {
                    "error": {
                        "message": "This model's maximum context length is 8192 tokens",
                        "type": "invalid_request_error",
                    }
                },
            ),
        )

        with pytest.raises(ProviderStreamError) as excinfo:
            self._call(monkeypatch, response)

        assert isinstance(excinfo.value, LLMError)
        assert excinfo.value.kind == ErrorKind.CONTEXT_LENGTH
        assert excinfo.value.provider == "openai"

    def test_anthropic_stream_error_event(self, monkeypatch):
        response = _FakeResponse(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            lines=_sse(
                {
                    "type": "error",
                    "error": {"type": "overloaded_error", "message": "Overloaded"},
                },
                event="error",
            ),
        )

        with pytest.raises(ProviderStreamError) as excinfo:
            self._call(
                monkeypatch,
                response,
                model="claude-opus-5-5",
                llm_base="https://api.anthropic.com",
                adapter=AnthropicAdapter(),
            )

        assert excinfo.value.kind == ErrorKind.SERVER
        assert excinfo.value.provider == "anthropic"
        assert "Overloaded" in str(excinfo.value)

    def test_stream_disconnect_is_normalised(self, monkeypatch):
        class _Broken(_FakeResponse):
            def iter_lines(self):
                raise requests.ConnectionError("Connection reset by peer")

        response = _Broken(status=200, headers={"Content-Type": "text/event-stream"})

        with pytest.raises(LLMError) as excinfo:
            self._call(monkeypatch, response)

        assert excinfo.value.kind == ErrorKind.STREAM
        assert excinfo.value.retryable

    def test_provider_stream_error_is_still_a_runtime_error(self):
        assert issubclass(ProviderStreamError, RuntimeError)
        # Backward-compatible import location.
        from utils.llm.base import ProviderStreamError as Legacy

        assert Legacy is ProviderStreamError


@pytest.mark.unit
class TestRender:
    def test_render_is_short_and_actionable(self):
        error = normalize_api_error(
            '{"error": {"message": "Missing Authentication header"}}',
            provider="kimi",
            model="kimi-k2",
            status_code=401,
            url="https://api.example/v1/chat/completions",
        )

        text = render_error(error)

        assert text.startswith("[LLM error] kimi/kimi-k2")
        assert "authentication failed" in text
        assert "HTTP 401" in text
        assert "Missing Authentication header" in text
        assert "hint:" in text
        assert "providers.json" in text

    def test_render_retryable_error_says_retry(self):
        error = normalize_api_error("upstream exploded", status_code=503)

        text = render_error(error)

        assert "retry" in text.lower()

    def test_render_long_message_is_clipped(self):
        error = normalize_api_error("x" * 2000, status_code=400)

        message_line = render_error(error).splitlines()[1]

        assert len(message_line) < 700

    def test_render_details_only_when_raw_exists(self):
        with_raw = normalize_api_error("boom", status_code=400)
        without_raw = LLMError("boom", status_code=400)

        assert "logged to" in render_error(with_raw)
        assert "logged to" not in render_error(without_raw)


@pytest.mark.unit
class TestErrorLog:
    def test_configure_error_logging_writes_once(self, error_log_in_tmp, caplog):
        from utils.llm import errors as errors_module

        error = normalize_api_error("boom", provider="p", model="m", status_code=400)
        with caplog.at_level(logging.ERROR):
            errors_module.log_error(error)

        content = error_log_in_tmp.read_text(encoding="utf-8")
        assert "Provider request failed" in content
        assert "kind=invalid_request" in content
        assert "Raw response body: boom" in content
        assert any("boom" in record.message for record in caplog.records)
        # A second call must not attach a duplicate handler.
        errors_module.log_error(error)
        assert (
            len(
                [
                    h
                    for h in logging.getLogger().handlers
                    if getattr(h, "_basic_agents_file", False)
                ]
            )
            == 1
        )


@pytest.mark.unit
class TestGenericAdapterStreamError:
    def test_plain_openai_compatible_server_error_chunk(self):
        response = _FakeResponse(lines=_sse({"error": "context length exceeded"}))

        with pytest.raises(ProviderStreamError) as excinfo:
            ModelAdapter().stream_and_collect(response)

        assert excinfo.value.kind == ErrorKind.CONTEXT_LENGTH
