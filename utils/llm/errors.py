"""Provider-neutral API error normalisation and rendering.

Every provider reports failures in its own dialect:

* OpenAI / DeepSeek / OpenRouter (and most OpenAI-compatible servers) return
  ``{"error": {"message": ..., "type": ..., "param": ..., "code": ...}}``.
* Anthropic returns ``{"type": "error", "error": {"type": ..., "message":
  ...}}``.
* Some gateways return a bare ``{"message": ..., "code": ...}`` or a plain
  string such as ``Authentication Fails (governor)``.
* Proxies in front of a provider may answer with an HTML error page, or even
  with HTTP 200 and a JSON error envelope.

:func:`normalize_api_error` folds all of those into one :class:`LLMError` that
carries the facts callers care about (status, category, retryability, the
offending parameter), and :func:`render_error` turns it into a short actionable
block for the terminal.  The untranslated body stays on ``error.raw`` and is
written to ``logs/app_errors.log`` by :func:`log_error`, so the wire format is
still available for debugging.
"""

from __future__ import annotations

import html
import json
import logging
import re

import requests

from cli.style import bold, dim, red, yellow
from utils.logging_setup import configure_error_logging, log_path_display

logger = logging.getLogger("basic_agents")

# Bodies can be huge (HTML error pages); keep the log useful but bounded.
_MAX_RAW = 4000
# The terminal message is a summary; the log keeps the rest.
_MAX_DISPLAY_MESSAGE = 600

# Phrases that identify a specific failure even when the provider sends a
# generic 400 (or an error event without any status code at all).
_CONTEXT_PHRASES = (
    "context length",
    "context window",
    "maximum context",
    "too many tokens",
    "prompt is too long",
    "input is too long",
    "request too large",
    "reduce the length",
    "exceeds the maximum",
)
_AUTH_PHRASES = (
    "authentication",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "incorrect api key",
    "invalid_api_key",
    "api key",
    "no cookie auth",
    "missing authentication",
    "credentials",
)
_QUOTA_PHRASES = ("insufficient quota", "billing", "out of credit", "credit balance")
_RATE_LIMIT_PHRASES = ("rate limit", "rate_limit", "too many requests")


class ErrorKind:
    """Coarse categories shared by every provider."""

    AUTH = "auth"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_LENGTH = "context_length"
    NOT_FOUND = "not_found"
    SERVER = "server"
    NETWORK = "network"
    TIMEOUT = "timeout"
    STREAM = "stream"
    EMPTY_RESPONSE = "empty_response"
    UNKNOWN = "unknown"


_LABELS = {
    ErrorKind.AUTH: "authentication failed",
    ErrorKind.QUOTA: "account out of quota",
    ErrorKind.RATE_LIMIT: "rate limited",
    ErrorKind.INVALID_REQUEST: "invalid request",
    ErrorKind.CONTEXT_LENGTH: "conversation too long",
    ErrorKind.NOT_FOUND: "model or endpoint not found",
    ErrorKind.SERVER: "provider unavailable",
    ErrorKind.NETWORK: "network error",
    ErrorKind.TIMEOUT: "request timed out",
    ErrorKind.STREAM: "stream aborted",
    ErrorKind.EMPTY_RESPONSE: "empty response",
    ErrorKind.UNKNOWN: "provider error",
}

_RETRYABLE = {
    ErrorKind.RATE_LIMIT,
    ErrorKind.SERVER,
    ErrorKind.NETWORK,
    ErrorKind.TIMEOUT,
    ErrorKind.STREAM,
}

# Anthropic and OpenAI both ship machine-readable type names; map the ones we
# know so the category does not depend on the HTTP status alone.
_TYPE_KINDS = {
    "authentication_error": ErrorKind.AUTH,
    "permission_error": ErrorKind.AUTH,
    "unauthorized": ErrorKind.AUTH,
    "invalid_api_key": ErrorKind.AUTH,
    "insufficient_quota": ErrorKind.QUOTA,
    "billing_error": ErrorKind.QUOTA,
    "rate_limit_error": ErrorKind.RATE_LIMIT,
    "rate_limit_exceeded": ErrorKind.RATE_LIMIT,
    "invalid_request_error": ErrorKind.INVALID_REQUEST,
    "context_length_exceeded": ErrorKind.CONTEXT_LENGTH,
    "not_found_error": ErrorKind.NOT_FOUND,
    "overloaded_error": ErrorKind.SERVER,
    "api_error": ErrorKind.SERVER,
    "timeout_error": ErrorKind.TIMEOUT,
}


class LLMError(RuntimeError):
    """A provider/API failure translated into one provider-neutral shape.

    ``message`` is the provider's own text (whitespace-collapsed), ``raw`` is
    the untouched body.  ``str(error)`` stays the message alone so existing
    generic handlers keep printing something sensible.
    """

    def __init__(
        self,
        message: str = "",
        *,
        kind: str = ErrorKind.UNKNOWN,
        provider: str = "",
        model: str = "",
        status_code: int | None = None,
        error_type: str | None = None,
        code=None,
        param: str | None = None,
        url: str | None = None,
        retry_after: float | None = None,
        raw: str | None = None,
    ):
        self.message = _collapse(message) or "Provider request failed"
        self.provider = provider or ""
        self.model = model or ""
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.param = param
        self.url = url
        self.retry_after = retry_after
        self.raw = raw
        self.kind = _refine_kind(kind, self.message, status_code, error_type)
        super().__init__(self.message)

    @property
    def label(self) -> str:
        return _LABELS.get(self.kind, _LABELS[ErrorKind.UNKNOWN])

    @property
    def retryable(self) -> bool:
        return self.kind in _RETRYABLE

    @property
    def hint(self) -> str | None:
        """One actionable sentence for the user, or ``None``."""
        who = self.provider or "this provider"

        if self.kind == ErrorKind.AUTH:
            return (
                f"Check `api_key` for provider '{who}' in providers.json "
                "(or pass --api-key)."
            )
        if self.kind == ErrorKind.QUOTA:
            return (
                f"The '{who}' account is out of credit/quota — check billing, "
                "or switch provider."
            )
        if self.kind == ErrorKind.RATE_LIMIT:
            if self.retry_after:
                return f"Wait about {self.retry_after:.0f}s, then retry the turn."
            return "Wait a moment, then retry the turn."
        if self.kind == ErrorKind.CONTEXT_LENGTH:
            return (
                "The conversation no longer fits the model's context window — "
                "start a new session (/new) or compress it "
                "(`session compress --id <id> --summarize`)."
            )
        if self.kind == ErrorKind.NOT_FOUND:
            return (
                "Check the model name and base URL against the provider's "
                "models list in providers.json."
            )
        if self.kind == ErrorKind.INVALID_REQUEST:
            if self.param == "reasoning_effort" or "reasoning_effort" in self.message:
                return (
                    "This model rejects `reasoning_effort` together with tools — "
                    "retry with `--reasoning-effort none`."
                )
            if self.param:
                return f"The provider rejected the `{self.param}` value."
            return (
                "The provider rejected the request; the message above names the "
                "offending field."
            )
        if self.kind == ErrorKind.NETWORK:
            target = self.url or who
            return (
                f"Could not reach {target} — check the base URL, network/VPN, "
                "and that a local server is running."
            )
        if self.kind == ErrorKind.TIMEOUT:
            return "Retry the turn, or lower the output token budget."
        if self.kind == ErrorKind.SERVER:
            return "Provider-side failure — retry shortly, or switch provider."
        if self.kind == ErrorKind.STREAM:
            return "The response ended mid-stream; the turn is incomplete — retry it."
        if self.kind == ErrorKind.EMPTY_RESPONSE:
            return (
                "The provider accepted the request but returned no data — retry, "
                "or check the model/server configuration."
            )
        return None


class ProviderStreamError(LLMError):
    """Raised when a provider reports an error inside a streaming response."""

    def __init__(self, message: str = "", **kwargs):
        kwargs.setdefault("kind", ErrorKind.STREAM)
        super().__init__(message, **kwargs)


# --------------------------------------------------------------- normalisation


def _collapse(text: str) -> str:
    """Join provider text into a single readable line."""
    return " ".join(str(text or "").split())


def _body_text(body) -> str:
    if body is None:
        return ""
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    if isinstance(body, (dict, list, tuple)):
        # Stream errors arrive already decoded; keep them JSON-readable in logs.
        try:
            return json.dumps(body, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(body)
    return str(body)


def _truncate(text: str, limit: int = _MAX_RAW) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"… ({len(text) - limit} chars truncated)"


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return _collapse(html.unescape(text))


def _fields_from(payload: dict) -> dict:
    """Extract message/type/code/param from one provider payload."""
    fields: dict = {}
    error = payload.get("error")

    if isinstance(error, str) and error.strip():
        fields["message"] = error
        source = payload
    else:
        source = error if isinstance(error, dict) else payload
        if not isinstance(source, dict):
            source = payload
        fields["message"] = (
            source.get("message")
            or source.get("msg")
            or source.get("detail")
            or source.get("error_description")
            or (payload.get("message") if source is not payload else None)
        )

    code = source.get("code", payload.get("code") if source is not payload else None)
    error_type = source.get("type") or source.get("error_type")
    if not error_type and isinstance(code, str):
        error_type = code
    fields["code"] = code
    fields["error_type"] = error_type
    fields["param"] = source.get("param") or payload.get("param")
    return fields


def _parse_body(body) -> tuple[dict, bool]:
    """Return ``(fields, is_error)`` for any response body.

    ``is_error`` is False for a JSON payload that is not an error envelope, so
    a 200 that merely is not an event stream is not misreported as a failure.
    """
    if isinstance(body, (dict, list, tuple)):
        payload = body
    else:
        text = _body_text(body).strip()
        if not text:
            # An empty body on a failed request is still a failure.
            return {"message": ""}, True

        payload = None
        if text[:1] in ("{", "["):
            try:
                payload = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                payload = None

        if payload is None:
            if text.startswith("<"):
                return {"message": _strip_html(text), "error_type": "html_error"}, True
            return {"message": _collapse(text)}, True

    if isinstance(payload, (list, tuple)):
        payload = payload[0] if payload and isinstance(payload[0], dict) else {}
        if not payload:
            return {"message": ""}, True

    if not isinstance(payload, dict):
        return {"message": _collapse(payload)}, True

    fields = _fields_from(payload)
    is_error = bool(
        fields.get("message")
        or fields.get("error_type")
        or payload.get("error") is not None
        or payload.get("type") == "error"
        or payload.get("object") == "error"
    )
    return fields, is_error


def is_error_payload(payload) -> bool:
    """Whether a decoded chunk/payload describes an error."""
    if isinstance(payload, dict):
        return bool(
            payload.get("error") is not None
            or payload.get("type") == "error"
            or payload.get("object") == "error"
        )
    return bool(payload)


def _refine_kind(
    kind: str, message: str, status_code: int | None, error_type: str | None
) -> str:
    """Pick the most specific category available."""
    text = (message or "").lower()

    if any(phrase in text for phrase in _CONTEXT_PHRASES):
        return ErrorKind.CONTEXT_LENGTH
    if kind not in (ErrorKind.UNKNOWN, ErrorKind.INVALID_REQUEST):
        # An explicitly requested kind (e.g. a stream error) wins.
        return kind

    mapped = _TYPE_KINDS.get((error_type or "").lower())
    if mapped:
        return mapped

    if status_code == 401 or status_code == 403:
        return ErrorKind.AUTH
    if status_code == 402:
        return ErrorKind.QUOTA
    if status_code == 404:
        return ErrorKind.NOT_FOUND
    if status_code == 408:
        return ErrorKind.TIMEOUT
    if status_code == 429:
        if any(phrase in text for phrase in _QUOTA_PHRASES):
            return ErrorKind.QUOTA
        return ErrorKind.RATE_LIMIT
    if status_code in (400, 422):
        return ErrorKind.INVALID_REQUEST
    if status_code is not None and status_code >= 500:
        return ErrorKind.SERVER

    # No usable status (stream error events, plain-text proxy bodies).
    if any(phrase in text for phrase in _AUTH_PHRASES):
        return ErrorKind.AUTH
    if any(phrase in text for phrase in _QUOTA_PHRASES):
        return ErrorKind.QUOTA
    if any(phrase in text for phrase in _RATE_LIMIT_PHRASES):
        return ErrorKind.RATE_LIMIT

    return kind


def normalize_api_error(
    body,
    *,
    provider: str = "",
    model: str = "",
    status_code: int | None = None,
    url: str | None = None,
    retry_after: float | None = None,
    kind: str | None = None,
    fallback: str | None = None,
) -> LLMError:
    """Translate any provider error body into a single :class:`LLMError`."""
    fields, is_error = _parse_body(body)
    if not is_error:
        fields = {"message": fallback or ""}

    code = fields.get("code")
    if (
        isinstance(code, int)
        and 100 <= code <= 599
        and (status_code is None or status_code < 400)
    ):
        # Gateways often put the real status inside the JSON envelope, even
        # when they answer HTTP 200 themselves.
        status_code = code
        status_code = code

    message = fields.get("message") or fallback or ""
    if not status_code:
        message = message or "Provider request failed"

    return LLMError(
        message or f"HTTP {status_code}",
        kind=kind or ErrorKind.UNKNOWN,
        provider=provider,
        model=model,
        status_code=status_code,
        error_type=fields.get("error_type"),
        code=code,
        param=fields.get("param"),
        url=url,
        retry_after=retry_after,
        raw=_truncate(_body_text(body)),
    )


def detect_api_error(
    body,
    *,
    provider: str = "",
    model: str = "",
    status_code: int | None = None,
    url: str | None = None,
) -> LLMError | None:
    """Normalise ``body`` only when it actually is an error envelope."""
    fields, is_error = _parse_body(body)
    if not is_error:
        return None
    return normalize_api_error(
        body,
        provider=provider,
        model=model,
        status_code=status_code,
        url=url,
    )


def stream_error(
    payload, *, provider: str = "", model: str = ""
) -> ProviderStreamError:
    """Build a :class:`ProviderStreamError` from an in-stream error payload.

    Provider type names (``overloaded_error``, ``rate_limit_error``, ...) still
    win over the generic "stream" category, so the hint stays specific.
    """
    error = normalize_api_error(payload, provider=provider, model=model)
    kind = error.kind if error.kind != ErrorKind.UNKNOWN else ErrorKind.STREAM
    return ProviderStreamError(
        error.message,
        kind=kind,
        provider=error.provider,
        model=error.model,
        status_code=error.status_code,
        error_type=error.error_type,
        code=error.code,
        param=error.param,
        retry_after=error.retry_after,
        raw=error.raw,
    )


def normalize_network_error(
    exc: Exception,
    *,
    provider: str = "",
    model: str = "",
    url: str | None = None,
    kind: str | None = None,
) -> LLMError:
    """Translate a ``requests`` failure into a :class:`LLMError`."""
    target = url or (provider or "the provider")
    if isinstance(exc, requests.Timeout):
        default_kind = ErrorKind.TIMEOUT
        message = f"Request to {target} timed out."
    elif isinstance(exc, requests.ConnectionError):
        default_kind = ErrorKind.NETWORK
        message = f"Could not connect to {target}: {exc}"
    else:
        default_kind = ErrorKind.NETWORK
        message = f"Request to {target} failed: {exc}"
    return LLMError(
        message,
        kind=kind or default_kind,
        provider=provider,
        model=model,
        url=url,
        raw=f"{exc.__class__.__name__}: {exc}",
    )


def retry_after_seconds(headers) -> float | None:
    """Parse a ``Retry-After`` header (seconds form) when present."""
    if not headers:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


# -------------------------------------------------------------------- logging


def log_error(error: LLMError, *, log: logging.Logger | None = None) -> None:
    """Write the full diagnostic (raw body included) to the error log."""
    configure_error_logging()
    log = log or logger
    lines = [
        "Provider request failed "
        f"(provider={error.provider or '?'} model={error.model or '?'} "
        f"kind={error.kind} status={error.status_code or '-'})"
    ]
    if error.url:
        lines.append(f"URL: {error.url}")
    if error.error_type or error.param or error.code is not None:
        lines.append(
            f"Type: {error.error_type or '-'} "
            f"param={error.param or '-'} code={error.code or '-'}"
        )
    lines.append(f"Message: {error.message}")
    if error.raw:
        lines.append(f"Raw response body: {error.raw}")
    log.error("\n".join(lines))


# ------------------------------------------------------------------ rendering


def _identity(error: LLMError) -> str:
    if error.provider and error.model:
        return f"{error.provider}/{error.model}"
    return error.provider or error.model or "provider"


def render_error(error: LLMError, *, show_details: bool = True) -> str:
    """Render one provider error as a short, actionable terminal block."""
    header = f"[LLM error] {_identity(error)} — {error.label}"
    if error.status_code:
        header += f" (HTTP {error.status_code})"

    lines = [bold(red(header)), f"  {_truncate(error.message, _MAX_DISPLAY_MESSAGE)}"]

    hint = error.hint
    if hint:
        lines.append("  " + yellow("hint:") + " " + hint)
    elif error.retryable:
        lines.append(dim("  this is usually temporary — retrying the turn may work"))

    if show_details and error.raw:
        lines.append(dim(f"  details: full response logged to {log_path_display()}"))
    return "\n".join(lines)
