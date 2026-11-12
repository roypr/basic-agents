import requests

_request_session = None


def get_request_session(api_key: str = ""):
    """Return the shared HTTP session.

    Authentication headers are supplied per request by the provider adapter.
    A shared session-level ``Authorization`` header is wrong when the REPL
    switches providers, and Anthropic uses a different auth scheme entirely.
    ``api_key`` is accepted for backward compatibility but is not stored.
    """
    global _request_session
    if _request_session is None:
        _request_session = requests.Session()
    return _request_session


def close_request_session():
    """Close the shared requests session to help abort active HTTP activity."""
    global _request_session
    if _request_session is not None:
        try:
            _request_session.close()
        except Exception:
            pass
        _request_session = None
