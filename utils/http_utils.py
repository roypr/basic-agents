import requests

_request_session = None


def get_request_session(api_key: str = ""):
    global _request_session
    if _request_session is None:
        _request_session = requests.Session()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        _request_session.headers.update(headers)
    return _request_session
