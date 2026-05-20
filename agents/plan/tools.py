import json
import subprocess
from pathlib import Path
from utils.file_utils import FILES_BASE_DIR, safe_path, ensure_base_dir_exists

def _safe_path(path: str) -> Path:
    return Path(safe_path(path))
# =========================================================
# Grep implementation (ripgrep-backed)
# =========================================================

def grep_search(
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    type: str | None = None,
    output_mode: str = "files_with_matches",
    case_insensitive: bool = False,
    multiline: bool = False,
    context: int = 0,
    line_numbers: bool = True,
    head_limit: int = 100
):
    """
    ripgrep-powered content search.
    """

    base = _safe_path(path)

    cmd = ["rg"]

    # Output modes
    if output_mode == "files_with_matches":
        cmd.append("-l")

    elif output_mode == "count":
        cmd.append("-c")

    # Context
    if context > 0:
        cmd.extend(["-C", str(context)])

    # Line numbers
    if line_numbers and output_mode == "content":
        cmd.append("-n")

    # Case insensitive
    if case_insensitive:
        cmd.append("-i")

    # Multiline
    if multiline:
        cmd.extend([
            "-U",
            "--multiline-dotall"
        ])

    # Glob filter
    if glob:
        cmd.extend(["--glob", glob])

    # Type filter
    if type:
        cmd.extend(["--type", type])

    cmd.append(pattern)
    cmd.append(str(base))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace"
        )

        output = result.stdout.strip()

        if not output:
            return []

        lines = output.splitlines()

        return lines[:head_limit]

    except FileNotFoundError:
        raise RuntimeError(
            "ripgrep (rg) is not installed"
        )

def get_all_files(base_dir: str = ".") -> str:
    abs_path = _safe_path(base_dir)
    all_files = []
    excluded_dirs = {".git", "node_modules", ".venv"}
    for full_path in abs_path.rglob("*"):
        if not full_path.is_file():
            continue
        rel = full_path.relative_to(abs_path)
        rel_parts = rel.parts
        if set(rel_parts) & excluded_dirs:
            continue
        if any(part.startswith('.') for part in rel_parts):
            continue
        all_files.append(rel.as_posix())
    return json.dumps(all_files)

def file_read(path: str) -> str:
    abs_path = _safe_path(path)
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()


def file_write(path: str, content: str) -> str:
    abs_path = _safe_path(path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
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

def finish(message: str = "") -> str:
    return message or "Done."

tools = [

    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": (
                "Search file contents using ripgrep. "
                "Supports regex patterns, file globs, type filtering, "
                "context lines, and multiple output modes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for"
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search"
                    },
                    "glob": {
                        "type": "string",
                        "description": "Glob filter like '*.py'"
                    },
                    "type": {
                        "type": "string",
                        "description": "File type filter like 'py', 'js'"
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": [
                            "content",
                            "files_with_matches",
                            "count"
                        ],
                        "default": "files_with_matches"
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "default": False
                    },
                    "multiline": {
                        "type": "boolean",
                        "default": False
                    },
                    "context": {
                        "type": "integer",
                        "default": 0
                    },
                    "line_numbers": {
                        "type": "boolean",
                        "default": True
                    },
                    "head_limit": {
                        "type": "integer",
                        "default": 100
                    }
                },
                "required": ["pattern"]
            }
        }
    },
    
   
    {
        "type" : "function",
        "function": {
            "name": "get_all_files",
            "description": "List accessible files inside the base directory.",
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
        "type" : "function",
        "function": {
            "name": "file_read",
            "description": "Read full content of a file, read_lines preferred.",
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
        "type" : "function",
        "function": {
            "name": "file_write",
            "description": "Create and write content to a new file.",
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
        "type" : "function",
        "function": {
            "name": "file_edit",
            "description": "Edit text inside a file, replace_lines preferred over this.",
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
        "type" : "function",
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
    }
]

TOOL_MAP = {
    "grep_search" : grep_search,
    "get_all_files": get_all_files,
    "file_read": file_read,
    "file_write": file_write,
    "file_edit": file_edit,
    "finish": finish,
}
