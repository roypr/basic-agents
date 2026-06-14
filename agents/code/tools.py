import subprocess
import ast
import re
from pathlib import Path
import shutil
from utils.file_utils import load_tool_definition, safe_path, _file_write, _file_edit, _replace_lines
from utils.tools_library import (get_current_date, web_search, request_get, get_all_files, 
                                 read_lines, file_read, file_delete, 
                                 glob_search, grep_search, run_command, finish)

EXTENSION_MAP = {
    ".py":   "python",
    ".php":  "php",
    ".js":   "javascript",
    ".mjs":  "javascript",
    ".cjs":  "javascript",
    ".jsx":  "javascript",
    ".ts":   "typescript",
    ".tsx":  "typescript",
    ".json": "json",
}

TS_MARKERS = (
    "tsconfig.json",
)

ESLINT_MARKERS = (
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.json",
    ".eslintrc.yml",
    "package.json",
)

PYTHON_MARKERS = (
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
)

JS_MARKERS = (
    *ESLINT_MARKERS,
    "package.json",
)

def find_project_dir(
    abs_path: str,
    markers: list[str] | tuple[str, ...],
) -> Path:
    """
    Find the nearest ancestor directory containing any of the given markers.

    Args:
        rel_path: Path relative to FILES_BASE_DIR.
        markers: Filenames indicating a project root.

    Returns:
        Path to the nearest matching directory, or FILES_BASE_DIR if none found.
    """
    base_dir = safe_path()
    path = Path(abs_path).resolve()

    current = path.parent if path.is_file() else path

    while True:
        if any((current / marker).exists() for marker in markers):
            return current

        if current == base_dir:
            return base_dir

        current = current.parent

def detect_language(path: str) -> str | None:
    """Detect language from file extension. Returns None if unrecognized."""
    ext = Path(path).suffix.lower()
    return EXTENSION_MAP.get(ext)


def _format_error(tool: str, output: str) -> str:
    """
    Normalize error output to a consistent format with line numbers prominently displayed.
    Handles ruff/eslint (file:line:col:), node (file:line:), PHP (on line N),
    and json.tool (line N column M) styles.
    Returns e.g. "Ruff error at line 5, col 8: E999 SyntaxError: invalid syntax"
    """
    text = output.strip()
    if not text:
        return f"{tool}: unknown error"

    # 1. file:line:col: message or file:line: message (ruff, eslint, node)
    m = re.search(r'\.\w+:(\d+)(?::(\d+))?:\s*(.*)', text)
    if m:
        line = m.group(1)
        col = f", col {m.group(2)}" if m.group(2) else ""
        msg = m.group(3).strip() or text
        return f"{tool} error at line {line}{col}: {msg}"

    # 2. "line N column M" (json.tool)
    m = re.search(r'line\s+(\d+)\s+column\s+(\d+)', text, re.IGNORECASE)
    if m:
        return f"{tool} error at line {m.group(1)}, col {m.group(2)}: {text}"

    # 3. "on line N" (PHP)
    m = re.search(r'on\s+line\s+(\d+)', text, re.IGNORECASE)
    if m:
        return f"{tool} error at line {m.group(1)}: {text}"

    # 4. "line N" as a last resort
    m = re.search(r'line\s+(\d+)', text, re.IGNORECASE)
    if m:
        return f"{tool} error at line {m.group(1)}: {text}"

    return f"{tool}: {text}"

def check_syntax(abs_path: str, language: str) -> str | None:
    """
    Run a syntax check appropriate for the language.
    Returns None if clean, or an error string if issues found.
    """

    if language == "python":
        if shutil.which("ruff"):
            # Auto-fix what we can first
            subprocess.run(
                ["ruff", "check", "--fix", abs_path],
                capture_output=True, text=True, timeout=15,
            )
            subprocess.run(
                ["ruff", "format", abs_path],
                capture_output=True, text=True, timeout=15,
            )

            # Now check for remaining issues
            result = subprocess.run(
                ["ruff", "check", abs_path],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return _format_error("Ruff", result.stdout.strip() or result.stderr.strip())

            result = subprocess.run(
                ["ruff", "format", "--check", abs_path],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return _format_error("Ruff format", result.stdout.strip() or result.stderr.strip())

            return None
        else:
            # ast.parse fallback (no subprocess, instant)
            try:
                source = Path(abs_path).read_text(encoding="utf-8", errors="replace")
                ast.parse(source, filename=abs_path)
                return None
            except SyntaxError as e:
                return f"SyntaxError: {e.msg} (line {e.lineno})"

    elif language == "php":
        if not shutil.which("php"):
            return None
        result = subprocess.run(
            ["php", "-l", abs_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            err = (result.stdout + result.stderr).strip()
            return _format_error("PHP", err)
        return None

    elif language == "json":
        if not shutil.which("python"):
            return None
        result = subprocess.run(
            ["python", "-m", "json.tool", abs_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return _format_error("JSON", result.stderr.strip() or result.stdout.strip())
        return None

    elif language == "javascript":
        # Try eslint first, fall back to node --check
        if shutil.which("eslint"):
            result = subprocess.run(
                ["eslint", "--no-eslintrc", "--rule", '{"no-undef":0}', abs_path],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return _format_error("ESLint", result.stdout.strip() or result.stderr.strip())
            return None

        elif shutil.which("node"):
            result = subprocess.run(
                ["node", "--check", abs_path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return _format_error("Node", result.stderr.strip())
            return None

        return None

    elif language == "typescript":
        # Try eslint first, fall back to tsc, then skip
        if shutil.which("eslint"):
            result = subprocess.run(
                ["eslint", "--no-eslintrc", "--rule", '{"no-undef":0}', abs_path],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return _format_error("ESLint", result.stdout.strip() or result.stderr.strip())
            return None

        elif shutil.which("npx"):
            result = subprocess.run(
                ["npx", "--yes", "-p", "typescript", "tsc", "--noEmit",
                 "--lib", "ES2020", "--target", "ES2020",
                 "--moduleResolution", "node",
                 "--skipLibCheck", "--strict", "false",
                 abs_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return _format_error("TypeScript", result.stderr.strip() or result.stdout.strip())
            return None

        return None

    return None  # Unsupported language

def syntax_check_file(rel_path: str) -> str | None:
    """
    Detect language from rel_path and run syntax check.
    Returns None if clean or unrecognized, error string if issues found.
    Designed to be called directly from file_write / file_edit.
    """
    language = detect_language(rel_path)
    if not language:
        return None

    abs_path = str(safe_path(rel_path))

    try:
        return check_syntax(abs_path, language)
    except subprocess.TimeoutExpired:
        return f"Syntax check timed out for {rel_path}"
    except Exception as e:
        return f"Syntax check failed: {e}"

def file_write(path: str, content: str) -> str:
    result = ""

    try:
        _file_write(path, content)
        result = f"Written to {path}"

        syntax_error = syntax_check_file(path)
        if syntax_error:
            result += f"\n\n⚠️ Syntax check: {syntax_error}"
        else:
            lang = detect_language(path)
            if lang:
                result += f"\n✓ Syntax OK ({lang})"

        print(f"[Tool: File write] {result}")
    except Exception as e:
        print(f"[Tool: File write] Error: {e}")
        result = str(e)
    return result

def file_edit(path: str, new_str: str, old_str: str = None) -> str:
    result = ""

    try:
        _file_edit(path, new_str, old_str)
        result = f"Edited {path}"

        syntax_error = syntax_check_file(path)
        if syntax_error:
            result += f"\n\n⚠️ Syntax check: {syntax_error}"
        else:
            lang = detect_language(path)
            if lang:
                result += f"\n✓ Syntax OK ({lang})"

        print(f"[Tool: File Edit] {result}")
    except Exception as e:
        print(f"[Tool: File Edit] Error: {e}")
        result = str(e)
    return result

def replace_lines(path: str, start_line: int, new_content: str, end_line: int = None) -> str:
    result = ""

    try:
        result = _replace_lines(path, start_line, new_content, end_line)

        syntax_error = syntax_check_file(path)
        if syntax_error:
            result += f"\n\n⚠️ Syntax check: {syntax_error}"
        else:
            lang = detect_language(path)
            if lang:
                result += f"\n✓ Syntax OK ({lang})"

        print(f"[Tool: Replace lines] {result}")
    except Exception as e:
        print(f"[Tool: Replace lines] Error: {e}")
        result = str(e)
    return result

all_tools = load_tool_definition()

tools = [
    all_tools["glob_search"],
    all_tools["grep_search"],
    all_tools["web_search"],
    all_tools["request_get"],
    all_tools["get_all_files"],
    all_tools["read_lines"],
    all_tools["file_read"],
    all_tools["file_delete"],
    all_tools["run_command"],
    all_tools["finish"],
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Create and write content to a new file. For Py, JS, TS, PHP files it will run quick syntax check",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path"
                    },
                    "content": {
                        "type": "string",
                        "description": "Text to write"
                    }
                },
                "required": [
                    "path",
                    "content"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_edit",
            "description": "Edit text inside a file, replace_lines preferred over this. For Py, JS, TS, PHP files it will run quick syntax check",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path"
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement content"
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Existing text to replace"
                    }
                },
                "required": [
                    "path",
                    "new_str"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_lines",
            "description": "Insert at perticular line of a file or replace a range of lines in a file. For Py, JS, TS, PHP files it will run quick syntax check",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Starting line number"
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Ending line number"
                    },
                    "new_content": {
                        "type": "string",
                        "description": "Replacement text"
                    }
                },
                "required": [
                    "path",
                    "start_line",
                    "new_content"
                ]
            }
        }
    }
]

TOOL_MAP = {
    "get_current_date" : get_current_date,
    "glob_search" : glob_search,
    "grep_search" : grep_search,
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
