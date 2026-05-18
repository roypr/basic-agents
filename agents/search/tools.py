import json
import requests
from ddgs import DDGS
from utils.html_utils import extract_text_from_html


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
        text = extract_text_from_html(resp.text)
    else:
        text = resp.text
    return text[:4000]


tools = [
    {
        "type" : "function",
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
        "type" : "function",
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
