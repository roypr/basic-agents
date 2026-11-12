## What's going wrong

I read the log and the tool code. The failures come from five causes, and the first one explains most of the "edit said OK but didn't apply" cases.

### 1. The syntax check silently rewrites your file (biggest cause)
`agents/code/tools.py:151-165` runs `ruff check --fix` and then `ruff format` on every edit. Two things follow:

- **`--fix` deletes imports that aren't used yet.** The model added an import in one edit and the code that uses it in the next. Ruff removed the "unused" import in between
, and the tool still reported `✓ Syntax OK`. The model then saw `F821 Undefined name` and concluded the edits were flaky.
  - It happened with `stream_error` in `anthropic.py`, `ErrorKind` in `llm_client.py`, and the imports in `main.py`, `session_utils.py` and `errors.py` (`import html`).
  - It also happened with `SAFE_TOOLS`/`McpConfig` in `base_agent.py`, and `McpError`/`describe_servers` in `main.py`.
  - In each case the same edit "worked" the second time, but only because the usage already existed by then.
- **`ruff format` reformats the whole file.** That is why the unrelated `filename = (...)` line in `session_utils.py` kept getting reflowed.

Fix in `check_syntax`:
```python
f"ruff check --fix --unfixable F401 {abs_path}"   # or drop --fix entirely
# drop the `ruff format {abs_path}` call; keep only `ruff format --check` as a warning
```
Better still, compare the file content before and after linting. If it changed, append `⚠️ ruff auto-modified this file` to the tool result.

### 2. `replace_lines` clobbers neighbouring lines
It replaces `start..end` inclusive, with no insert mode and no preview. Most of the damage came from four patterns:

- **Stale line numbers.** The model reused numbers after an earlier edit shifted them. For example, `stream_error` was replaced from line 466 when it actually started at 46
7, and the readme duplicate-heading fix hit the wrong lines.
- **Wrong `end_line`.**
  - `errors.py` 422-443 wiped the `return LLMError(...)` block.
  - `base_agent.py` 63-75 dropped the closing `)` of `get_adapter(`.
  - `base_agent.py` 41-43 dropped the `provider_type` parameter.
  - `repl.py` 241-243 dropped `old = self.agent`.
  - `readme.md` 25-43 swallowed the requirements lines.
- **Repeating context lines.** The new content included a line that was already outside the range, giving duplicates like `def build_parser` twice, `shutdown_tool_executor(
)` twice and `text = ...` twice.
- **Insert attempts.** Calling it with `start=48, end=47` fails with `Invalid line range`. The description says "Insert at perticular line", but there is no insert mode.

Fixes:
- Rewrite the description in `utils/tool_definition.json`. It should say: "Replaces lines start_line..end_line inclusive (end_line defaults to start_line). To insert before
 line N, replace line N with your new text plus the original line N. Don't repeat lines outside the range. Line numbers shift after every edit, so re-read first."
- Return a preview, which makes a clobber visible immediately:
  ```python
  # in _replace_lines, after writing:
  s = max(0, start_line - 3); e = start_line - 1 + len(new_lines) + 2
  preview = "".join(f"{i+1}: {l}" for i, l in enumerate(result[s:e], start=s))
  return f"{action} {path} (...)\n--- result ---\n{preview}"
  ```
- Also return the old text that was removed, for example the first 5 lines.
- Raise an error when `end_line > total_lines` instead of clamping with `min()` (lines 221 and 248).

### 3. The model used the wrong argument name for `replace_lines`
Three calls failed with `Missing required arguments ... new_content`. The model sent `new_str` and `content`, which are the parameter names of `file_edit` and `file_write`.
 Options:
- Accept aliases (`new_str`, `content`) in the `replace_lines` wrapper.
- Or make the error say "replace_lines takes `new_content`, not `new_str`".

### 4. `file_edit` has a dangerous fallback (latent bug)
`utils/file_utils.py:162-166`: if `old_str` isn't found, `new_str` is appended to the end of the file and the tool reports success. A typo in `old_str` gives a valid-lookin
g file with a stray block at the bottom. I can't prove this caused a specific failure in this log, but it matches the "edit reported OK, wrong result" pattern. Change it to
:
```python
if old_str is None:
    updated = original + ("" if original.endswith("\n") or not original else "\n") + new_str
elif old_str not in original:
    raise ValueError("old_str not found in file (no changes made)")
elif original.count(old_str) > 1:
    raise ValueError("old_str matches multiple places; add more context")
else:
    updated = original.replace(old_str, new_str, 1)
```

### 5. Prompt guidance conflicts with the tools
The description of `file_edit` says "replace_lines preferred", which pushes the model towards the fragile tool. The model also fell back to `python - <<EOF ... str.replace`
 through `run_command`, and that was the only method that never failed. Suggested rules for the agent's `system_prompt.txt`:
- Prefer `file_edit` with a unique `old_str` anchor. Use `replace_lines` only for large blocks.
- Run `read_lines` immediately before every `replace_lines` and again after each edit.
- When adding an import, add it in the same edit as its first use, or add the usage first.
- Never change the same file from stale line numbers.

## Suggested order
1. Fix #1 (`--unfixable F401`, no whole-file format). It is a one-line change and removes most errors.
2. Fix #4 (raise instead of appending).
3. Add the preview and the clearer description for `replace_lines` (#2 and #3).