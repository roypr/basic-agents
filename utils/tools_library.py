import json
import os
import requests
from ddgs import DDGS
import subprocess
import fnmatch
from datetime import datetime, timezone
from pathlib import Path
import shutil
from .html_utils import extract_text_from_html
from .file_utils import safe_path, FILES_BASE_DIR, ensure_base_dir_exists

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
        text = extract_text_from_html(resp.text)
    else:
        text = resp.text
    return text[:4000]

def finish(message: str = "") -> str:
    return message or "Done."

def get_all_files(base_dir: str = ".") -> str:
    abs_path = safe_path(base_dir)
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
    abs_path = safe_path(path)
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()


def file_write(path: str, content: str) -> str:
    abs_path = safe_path(path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Wrote {len(content)} chars to {path}"


def file_edit(path: str, new_str: str, old_str: str = None) -> str:
    abs_path = safe_path(path)
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
    abs_path = safe_path(path)
    if not abs_path.exists():
        return f"Error: {path} does not exist"
    if abs_path.is_dir():
        if recursive:
            shutil.rmtree(abs_path)
            return f"Deleted directory {path} and all contents"
        abs_path.rmdir()
        return f"Deleted empty directory {path}"
    abs_path.unlink()
    return f"Deleted file {path}"

def read_lines(path: str, start_line: int, end_line: int) -> str:
    abs_path = safe_path(path)
    if not abs_path.exists():
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


def replace_lines(path: str, start_line: int, new_content: str, end_line: int = None) -> str:
    abs_path = safe_path(path)
    with open(abs_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    total_lines = len(lines)

    if end_line is None:
        # Insert mode: inject at start_line, shift rest down
        if start_line < 1 or start_line > total_lines + 1:
            raise ValueError("Invalid start_line for insertion")
        result = lines[:start_line - 1] + new_content.splitlines(keepends=True) + lines[start_line - 1:]
    else:
        # Replace mode: original behaviour
        if start_line < 1 or end_line < 1 or start_line > end_line or end_line > total_lines:
            raise ValueError("Invalid line range")
        result = lines[:start_line - 1] + new_content.splitlines(keepends=True) + lines[end_line:]

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(''.join(result))

    action = "Inserted at" if end_line is None else f"Replaced lines {start_line}-{end_line} in"
    return f"{action} line {start_line} in {path}"

# =========================================================
# Glob implementation
# =========================================================

def glob_search(
    pattern: str,
    path: str | None = None,
    limit: int = 100
):
    """
    Fast glob-based file search.
    """

    base = safe_path(path)

    matches = []

    # Skip common junk and hidden files/dirs
    skip_dirs = {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build"
    }

    for full_path in base.rglob("*"):
        if not full_path.is_file():
            continue

        rel_path = full_path.relative_to(base)
        rel_parts = rel_path.parts

        if set(rel_parts) & skip_dirs:
            continue
        if any(part.startswith('.') for part in rel_parts):
            continue

        if fnmatch.fnmatch(rel_path.as_posix(), pattern):
            mtime = full_path.stat().st_mtime
            matches.append(
                {
                    "path": rel_path.as_posix(),
                    "mtime": mtime
                }
            )

    matches.sort(
        key=lambda x: x["mtime"],
        reverse=True
    )

    return [
        m["path"]
        for m in matches[:limit]
    ]


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

    base = safe_path(path)

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

def find_bash():
    if os.name != "nt":
        return None

    candidates = [
        r"C:\\Program Files\\Git\\bin\\bash.exe",
        r"C:\\Program Files (x86)\\Git\\bin\\bash.exe",
        shutil.which("bash"),
    ]

    for p in candidates:
        if p and Path(p).exists():
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