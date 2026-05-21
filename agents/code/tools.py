import subprocess
import ast
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
    ".ts":   "typescript",
    ".tsx":  "typescript",
    ".jsx":  "javascript",
}

def detect_language(path: str) -> str | None:
    """Detect language from file extension. Returns None if unrecognized."""
    ext = Path(path).suffix.lower()
    return EXTENSION_MAP.get(ext)

def check_syntax(abs_path: str, language: str) -> str | None:
    """
    Run a syntax check appropriate for the language.
    Returns None if clean, or an error string if issues found.
    """

    if language == "python":
        # Use ast.parse first (no subprocess, instant)
        try:
            source = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            ast.parse(source, filename=abs_path)
            return None
        except SyntaxError as e:
            return f"SyntaxError: {e.msg} (line {e.lineno})"

    elif language == "php":
        if not shutil.which("php"):
            return None  # php not installed, skip silently
        result = subprocess.run(
            ["php", "-l", abs_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            err = (result.stdout + result.stderr).strip()
            return f"PHP syntax error: {err}"
        return None

    elif language in ("javascript", "typescript"):
        # Try eslint first, fall back to node --check
        if shutil.which("eslint"):
            result = subprocess.run(
                ["eslint", "--no-eslintrc", "--rule", '{"no-undef":0}', abs_path],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                return f"ESLint: {result.stdout.strip() or result.stderr.strip()}"
            return None

        elif shutil.which("node"):
            result = subprocess.run(
                ["node", "--check", abs_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return f"JS syntax error: {result.stderr.strip()}"
            return None

        return None  # Neither available, skip silently

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
