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
from .file_utils import (safe_path, FILES_BASE_DIR, ensure_base_dir_exists, 
                         _file_read, _file_write, _file_edit,
                         _read_lines, _replace_lines)

def get_current_date() -> str:
    now = datetime.now(timezone.utc)
    result = now.strftime("UTC: %Y-%m-%d %H:%M:%S")
    print(f"[Tool: Date] {result}")
    return result

def web_search(query: str, page: int = 1, results_per_page: int = 10) -> str:
    with DDGS() as ddgs:
        all_results = list(ddgs.text(query, max_results=page * results_per_page))
    page_results = all_results[(page - 1) * results_per_page: page * results_per_page]
    results = [
        {"title": r["title"], "snippet": r["body"], "href": r["href"]}
        for r in page_results
    ]
    print(f"[Tool: Web Search] Results: {len(results)}")
    for r in results:
        snippet_preview = r["snippet"].replace("\n", " ").strip()
        if len(snippet_preview) > 100:
            snippet_preview = snippet_preview[:100].rstrip() + "..."
        print(f"{r['title']}: {snippet_preview}")

    return json.dumps(results)


def request_get(url: str) -> str:
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if "html" in content_type:
        text = extract_text_from_html(resp.text)
    else:
        text = resp.text
    
    print(f"[Tool: Web Page] Url: {url}")
    return text[:4000]

def finish(message: str = "") -> str:
    print(f"[Finish] {message}")
    
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

    print(f"[Tool: Get Files] {len(all_files)} Files found")
    return json.dumps(all_files)


def file_read(path: str) -> str:
    result = ""

    try:
        result = _file_read(path)
        print(f"[Tool: File read] path: {path}")
    except Exception as e:
        print(f"[Tool: File read] Error: {e}")
        result = str(e)
    return result

def file_write(path: str, content: str) -> str:
    result = ""

    try:
        _file_write(path, content)
        result = f"Written to {path}"
        print(f"[Tool: File write] path: {path}")
    except Exception as e:
        print(f"[Tool: File write] Error: {e}")
        result = str(e)
    return result


def file_edit(path: str, new_str: str, old_str: str = None) -> str:
    result = ""

    try:
        _file_edit(path, new_str, old_str)
        result = f"Edited {path}"
        print(f"[Tool: File Edit] path: {path}")
    except Exception as e:
        print(f"[Tool: File Edit] Error: {e}")
        result = str(e)
    return result


def file_delete(path: str, recursive: bool = False) -> str:
    result = ""
    abs_path = safe_path(path)
    if not abs_path.exists():
        result = f"Error: {path} does not exist"
    if abs_path.is_dir():
        if recursive:
            shutil.rmtree(abs_path)
            result = f"Deleted directory {path} and all contents"
        abs_path.rmdir()
        result = f"Deleted empty directory {path}"
    abs_path.unlink()
    result = f"Deleted file {path}"
    print(f"[Tool: File Delete] {result}")
    return result

def read_lines(path: str, start_line: int, end_line: int) -> str:
    result = ""

    try:
        result = _read_lines(path, start_line, end_line)
        print(f"[Tool: Read lines] path: {path} lines: {start_line}-{end_line}")
    except Exception as e:
        print(f"[Tool: Read lines] Error: {e}")
        result = str(e)
    return result


def replace_lines(path: str, start_line: int, new_content: str, end_line: int = None) -> str:
    result = ""

    try:
        result = _replace_lines(path, start_line, new_content, end_line)
        print(f"[Tool: Replace lines] path: {path} start: {start_line} end: {end_line}")
    except Exception as e:
        print(f"[Tool: Replace lines] Error: {e}")
        result = str(e)
    return result

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

    results = [
        m["path"]
        for m in matches[:limit]
    ]

    print(f"[Tool: Glob] {len(results)} matches")
    return results

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

    ensure_base_dir_exists()                     # ← match run_command
    base = safe_path(path)

    cmd = ["rg"]

    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")

    if context > 0:
        cmd.extend(["-C", str(context)])

    if line_numbers and output_mode == "content":
        cmd.append("-n")

    if case_insensitive:
        cmd.append("-i")

    if multiline:
        cmd.extend(["-U", "--multiline-dotall"])

    if glob:
        cmd.extend(["--glob", glob])

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
            errors="replace",
            cwd=FILES_BASE_DIR,                  # ← match run_command
            timeout=30,                          # ← match run_command
        )

        # rg exit codes: 0 = matches found, 1 = no matches, 2 = error
        if result.returncode == 2:               # ← distinguish real errors
            err = result.stderr.strip()
            return f"Error: {err}" if err else "Error: rg exited with code 2"

        output = result.stdout.strip()

        if not output:
            return []

        lines = output.splitlines()
        return lines[:head_limit]

    except subprocess.TimeoutExpired:            # ← match run_command
        return "Error: grep search timed out after 30 seconds."
    except FileNotFoundError:
        return "Error: ripgrep (rg) is not installed."  # ← return, don't raise
    except Exception as e:
        return f"Error running grep search: {e}"        # ← match run_command