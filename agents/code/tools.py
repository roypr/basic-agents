import json
import os
import re
import requests
import shutil
import subprocess
from datetime import datetime, timezone
from html.parser import HTMLParser
from ddgs import DDGS
from utils.file_utils import FILES_BASE_DIR, safe_path, ensure_base_dir_exists


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


def validate_args(tool_name: str, args: dict, tools: list) -> None:
    tool_def = next((tool for tool in tools if tool["function"]["name"] == tool_name), None)
    if not tool_def:
        raise ValueError(f"Unknown tool: {tool_name}")
    required_params = tool_def["function"].get("parameters", {}).get("required", [])
    missing = [param for param in required_params if param not in args]
    if missing:
        raise ValueError(f"Missing required arguments for '{tool_name}': {', '.join(missing)}")


def get_current_date() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("UTC: %Y-%m-%d %H:%M:%S")


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
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = resp.text
    return text[:4000]


def _safe_path(path: str) -> str:
    return safe_path(path)


def get_all_files(base_dir: str = ".") -> str:
    abs_path = _safe_path(base_dir)
    all_files = []
    excluded_dirs = {".git", "node_modules", ".venv"}
    for root, dirs, files in os.walk(abs_path):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        for file in files:
            full_path = os.path.join(root, file)
            rel = os.path.relpath(full_path, abs_path)
            all_files.append(rel)
    return json.dumps(all_files)


def file_read(path: str) -> str:
    abs_path = _safe_path(path)
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()


def file_write(path: str, content: str) -> str:
    abs_path = _safe_path(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Wrote {len(content)} chars to {path}"


def file_edit(path: str, new_str: str, old_str: str = None) -> str:
    abs_path = _safe_path(path)
    with open(abs_path, "r", encoding="utf-8") as f:
        original = f.read()
    if old_str is None or old_str not in original:
        updated = f"\n{new_str}"
    else:
        updated = original.replace(old_str, new_str, 1)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(updated)
    return f"Edited {path}"


def file_delete(path: str, recursive: bool = False) -> str:
    abs_path = _safe_path(path)
    if not os.path.exists(abs_path):
        return f"Error: {path} does not exist"
    if os.path.isdir(abs_path):
        if recursive:
            shutil.rmtree(abs_path)
            return f"Deleted directory {path} and all contents"
        os.rmdir(abs_path)
        return f"Deleted empty directory {path}"
    os.remove(abs_path)
    return f"Deleted file {path}"


def read_lines(path: str, start_line: int, end_line: int) -> str:
    abs_path = _safe_path(path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {abs_path}")
    with open(abs_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    total_lines = len(lines)
    if start_line < 1 or end_line < 1:
        raise ValueError("Line numbers must be positive integers.")
    if start_line > end_line:
        raise ValueError("Start line must be less than or equal to end line.")
    if start_line > total_lines or end_line > total_lines:
        raise ValueError(f"Requested line range exceeds file length ({total_lines} lines).")
    return ''.join(lines[start_line - 1:end_line])


def replace_lines(path: str, start_line: int, end_line: int, new_content: str) -> str:
    abs_path = _safe_path(path)
    with open(abs_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    total_lines = len(lines)
    if start_line < 1 or end_line < 1 or start_line > end_line or end_line > total_lines:
        raise ValueError("Invalid line range")
    result = lines[:start_line - 1] + new_content.splitlines(keepends=True) + lines[end_line:]
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(''.join(result))
    return f"Replaced lines {start_line}-{end_line} in {path}"


def find_bash():
    if os.name != "nt":
        return None

    candidates = [
        r"C:\\Program Files\\Git\\bin\\bash.exe",
        r"C:\\Program Files (x86)\\Git\\bin\\bash.exe",
        shutil.which("bash"),
    ]

    for p in candidates:
        if p and os.path.exists(p):
            return p

    return None


def run_command(command: str) -> str:
    blocked = ["rm -rf", "mkfs", "dd if=", ":(){:|:&};:"]
    for b in blocked:
        if b in command:
            return f"Blocked: '{b}' is not allowed."

    ensure_base_dir_exists()
    bash = find_bash()

    if bash and os.name == "nt":
        cmd = [bash, "-c", command]
        shell = False
    else:
        cmd = command
        shell = True

    try:
        result = subprocess.run(
            cmd,
            shell=shell,
            cwd=FILES_BASE_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = []
        if result.stdout:
            output.append(f"stdout:\n{result.stdout.strip()}")
        if result.stderr:
            output.append(f"stderr:\n{result.stderr.strip()}")
        if result.returncode != 0:
            output.append(f"exit code: {result.returncode}")
        return "\n".join(output) if output else "(command produced no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 30 seconds."
    except Exception as e:
        return f"Error running command: {e}"


def finish(message: str = "") -> str:
    return message or "Done."


tools = [
    {
        "function": {
            "name": "get_current_date",
            "description": "Return the current UTC date and time.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
    },
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
            "description": "Retrieve a public URL and return text content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch"},
                },
                "required": ["url"],
            },
        }
    },
    {
        "function": {
            "name": "get_all_files",
            "description": "List accessible files under the file base directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base_dir": {"type": "string", "description": "Base path relative to file root"},
                },
                "required": [],
            },
        }
    },
    {
        "function": {
            "name": "file_read",
            "description": "Read a file from the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                },
                "required": ["path"],
            },
        }
    },
    {
        "function": {
            "name": "file_write",
            "description": "Write content to a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "content": {"type": "string", "description": "Text to write"},
                },
                "required": ["path", "content"],
            },
        }
    },
    {
        "function": {
            "name": "file_edit",
            "description": "Edit text inside a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "new_str": {"type": "string", "description": "Replacement content"},
                    "old_str": {"type": "string", "description": "Existing text to replace"},
                },
                "required": ["path", "new_str"],
            },
        }
    },
    {
        "function": {
            "name": "file_delete",
            "description": "Delete a file or directory within the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file or directory path"},
                    "recursive": {"type": "boolean", "description": "Remove directories recursively"},
                },
                "required": ["path"],
            },
        }
    },
    {
        "function": {
            "name": "read_lines",
            "description": "Read a range of lines from a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "start_line": {"type": "integer", "description": "Starting line number"},
                    "end_line": {"type": "integer", "description": "Ending line number"},
                },
                "required": ["path", "start_line", "end_line"],
            },
        }
    },
    {
        "function": {
            "name": "replace_lines",
            "description": "Replace a range of lines in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                    "start_line": {"type": "integer", "description": "Starting line number"},
                    "end_line": {"type": "integer", "description": "Ending line number"},
                    "new_content": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "start_line", "end_line", "new_content"],
            },
        }
    },
    {
        "function": {
            "name": "run_command",
            "description": "Run a shell command from the workspace base directory and return stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["command"],
            },
        }
    },
    {
        "function": {
            "name": "finish",
            "description": "Finish the task and return a final summary message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Optional final message"},
                },
                "required": [],
            },
        }
    },
]

TOOL_MAP = {
    "get_current_date": get_current_date,
    "web_search": web_search,
    "request_get": request_get,
    "get_all_files": get_all_files,
    "file_read": file_read,
    "file_write": file_write,
    "file_edit": file_edit,
    "file_delete": file_delete,
    "read_lines": read_lines,
    "replace_lines": replace_lines,
    "run_command": run_command,
    "finish": finish,
}
