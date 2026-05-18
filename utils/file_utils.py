import os
from pathlib import Path
from config import FILES_BASE_DIR


def safe_path(path: str) -> str:
    base_dir = Path(FILES_BASE_DIR).resolve()
    candidate = (base_dir / path).resolve()
    if base_dir not in [candidate, *candidate.parents]:
        raise ValueError(f"Path '{path}' escapes the allowed base directory")
    return str(candidate)


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
