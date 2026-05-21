import os
import json
from pathlib import Path
from config import FILES_BASE_DIR


def safe_path(path: str) -> Path:
    base_dir = Path(FILES_BASE_DIR).resolve()
    candidate = (base_dir / path).resolve()
    if base_dir not in [candidate, *candidate.parents]:
        raise ValueError(f"Path '{path}' escapes the allowed base directory")
    return candidate


def ensure_base_dir_exists() -> None:
    os.makedirs(FILES_BASE_DIR, exist_ok=True)


def parse_line_range(value: str):
    if value is None:
        return None

    value = value.strip()
    if not value:
        return None

    if "-" in value:
        start_str, end_str = value.split("-", 1)
        try:
            start = int(start_str.strip()) if start_str.strip() else 1
            end = int(end_str.strip())
        except ValueError as exc:
            raise ValueError("Invalid --lines format. Use 10-20 or 20.") from exc
    else:
        try:
            start = 1
            end = int(value)
        except ValueError as exc:
            raise ValueError("Invalid --lines format. Use 10-20 or 20.") from exc

    if start < 1 or end < 1 or start > end:
        raise ValueError("Invalid --lines range. Use a positive range like 10-20 or 20.")

    return start, end


def read_file_section(path: str, line_range):
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Included file not found: {path}")

    text = file_path.read_text(encoding="utf-8")
    if line_range is None:
        return text

    lines = text.splitlines(keepends=True)
    start, end = line_range
    if start > len(lines):
        raise ValueError(
            f"Requested line range {start}-{end} exceeds file length ({len(lines)} lines)."
        )

    end = min(end, len(lines))
    return "".join(lines[start - 1 : end])


def build_query(query: str, include_path: str | None, line_range):
    if include_path is None:
        return query

    included_text = read_file_section(include_path, line_range)
    range_label = (
        f"Lines {line_range[0]}-{line_range[1]}" if line_range else "Whole file"
    )
    include_block = (
        f"Included file content from {include_path} ({range_label}):\n{included_text}"
    )

    if query and query.strip():
        return f"{query.strip()}\n\n{include_block}"
    return include_block

def load_tool_definition() -> dict:
    json_path = Path("utils/tool_definition.json").resolve()
    try:
        with open(json_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError("The file 'tool_definition.json' was not found.")
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Error decoding JSON from 'tool_definition.json': {e}") from e
    
def _file_read(path: str) -> str:
    abs_path = safe_path(path)
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()

def _file_write(path: str, content: str):
    abs_path = safe_path(path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)

def _file_edit(path: str, new_str: str, old_str: str = None):
    abs_path = safe_path(path)
    with open(abs_path, "r", encoding="utf-8") as f:
        original = f.read()
    if old_str is None or old_str not in original:
        if original and not original.endswith("\n"):
            updated = original + "\n" + new_str
        else:
            updated = original + new_str
    else:
        updated = original.replace(old_str, new_str, 1)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(updated)


def _read_lines(path: str, start_line: int, end_line: int) -> str:
    abs_path = safe_path(path)
    if not Path(abs_path).exists():
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


def _replace_lines(path: str, start_line: int, new_content: str, end_line: int = None) -> str:
    abs_path = safe_path(path)
    with open(abs_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    total_lines = len(lines)

    if end_line is None:
        if start_line < 1 or start_line > total_lines + 1:
            raise ValueError("Invalid start_line for insertion")
        result = lines[:start_line - 1] + new_content.splitlines(keepends=True) + lines[start_line - 1:]
    else:
        if start_line < 1 or end_line < 1 or start_line > end_line or end_line > total_lines:
            raise ValueError("Invalid line range")
        result = lines[:start_line - 1] + new_content.splitlines(keepends=True) + lines[end_line:]

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(''.join(result))

    action = "Inserted at" if end_line is None else f"Replaced lines {start_line}-{end_line} in"
    return f"{action} line {start_line} in {path}"

    