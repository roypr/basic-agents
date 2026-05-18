import json
import requests
from ddgs import DDGS
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "head", "noscript", "svg"}

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data):
        if not self._skip_depth:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def get_text(self):
        return "\n".join(self._parts)


def web_search(query: str, page: int = 1, results_per_page: int = 10) -> str:
    with DDGS() as ddgs:
        all_results = list(ddgs.text(query, max_results=page * results_per_page))
    page_results = all_results[(page - 1) * results_per_page: page * results_per_page]
    return json.dumps([
        {"title": r["title"], "snippet": r["body"], "href": r["href"]}
        for r in page_results
    ])


def request_get(url: str) -> str:
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if "html" in content_type:
        extractor = _TextExtractor()
        extractor.feed(resp.text)
        text = extractor.get_text()
    else:
        text = resp.text
    return text[:4000]


tools = [
    {
        "function": {
            "name": "web_search",
            "description": "Search the web and return JSON-formatted results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "page": {"type": "integer", "description": "Page number"},
                    "results_per_page": {"type": "integer", "description": "Results count per page"},
                },
                "required": ["query"],
            },
        }
    },
    {
        "function": {
            "name": "request_get",
            "description": "Retrieve a URL and return its text content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                },
                "required": ["url"],
            },
        }
    },
]

TOOL_MAP = {
    "web_search": web_search,
    "request_get": request_get,
}
