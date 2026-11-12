# MCP Phase 2 — status (complete)

Originally written as a pause/handoff note while one startup test was failing
and a pytest run hung. Both are resolved; this file is now the record of what
was wrong, what was fixed, and how it was verified.

## 1. Scope of phase 2

1. HTTP transport (Streamable HTTP) + static auth headers for remote servers
2. Resources / prompts exposed as synthetic tools
3. `python main.py mcp list` subcommand and `/mcp` REPL command
   (`/mcp`, `/mcp reconnect [name]`)

All three are implemented and verified (see §4).

## 2. The blocker and its root cause

**Symptom:** `tests/unit/test_mcp_connection.py::TestStartup::test_tools_are_listed_across_pages`
failed with `AttributeError: 'McpServer' object has no attribute 'tools'`, and
`test_missing_sdk_is_reported_with_a_hint` hung until killed.

**Cause:** the `_cleanup_failed_start()` insertion swallowed the
`@property def tools` header and body. Two consequences:

* `McpServer.tools` no longer existed (the `AttributeError`).
* The property's leftover tail (`if not self._started: self.start()` /
  `return self._tools`) was appended to `_cleanup_failed_start()`, so any
  failed start called `start()` **while `start()` held the non-reentrant
  `_start_lock`** → permanent deadlock (the hang; not a harness artifact).

**Fix:** restored `start()` and `tools` as separate members; documented that
`_cleanup_failed_start()` runs under `_start_lock` and must not call
`start()`; included `_http_client_cm` in its teardown condition so an
HTTP client opened before a failed handshake is still closed.

**Regression test:** `test_failed_handshake_tears_down_the_transport`
(asserts transport/session closed, loop stopped, and that a retry fails
promptly instead of deadlocking). Verified to fail with the cleanup disabled.

## 3. Real bugs found while verifying against the SDK

1. **`McpError.render()` assigned `header` twice**, so the second line
   discarded the `who` label and config errors printed
   `[MCP error] server — config` instead of `[MCP error] mcp.json — config`.
   `render()` had no test coverage at all; three tests added.
2. **v2 pagination dialect.** mcp 2.x replaced `list_tools(cursor=...)` with
   `list_tools(*, params=PaginatedRequestParams)`. Passing `cursor=` raised
   `TypeError`, so any server with more than one page of tools failed to
   connect. Now dispatched by signature (`params=` when the method accepts it,
   `cursor=` otherwise) via `_pagination_params()`.
3. **HTTP client dialect.** `http_client=` is typed `httpx2.AsyncClient` on
   mcp 2.x but plain `httpx.AsyncClient` on mcp 1.30 — the old code always
   imported `httpx2`, so HTTP + `headers` failed on 1.30 with a misleading
   "this 'mcp' version provides no HTTP client" hint. `_http_client_module()`
   now picks the module from the factory's own annotation.
4. **Legacy function spelling.** mcp 1.2–1.2x live in
   `mcp.client.streamable_http` but spell the function
   `streamablehttp_client`. The previous lookup only checked
   `streamable_http_client` in that module, so HTTP looked unsupported on
   those versions. `_first_attr()` now tries both modules × both spellings.
5. **Message-less exceptions.** mcp 1.x raises bare transport errors, which
   rendered as `MCP server 'x' failed:` with nothing after the colon. The
   exception type name is now used as the fallback detail.
6. **Connect hint was stdio-only.** `"check that the command exists on PATH"`
   was shown for HTTP/SSE failures too; the hint is now transport-aware.

## 4. Verification

**Unit/integration suite:** `python -m pytest -q -p no:cacheprovider --no-cov`
→ **314 passed** (was 300 before this phase's verification work).
`python -m ruff check .` → only the 2 pre-existing findings
(`run_tests.py: sys`, `tests/unit/test_file_utils.py: pytest`).

Per-file counts: `test_mcp_config` 32, `test_mcp_connection` 34,
`test_mcp_manager` 26, `test_mcp_adapter` 17, `test_repl` 37, `test_main` 15.

**Real SDK matrix** (throwaway venvs, project venv untouched; harness kept in
`tests/manual/mcp/`):

| SDK | stdio | Streamable HTTP | SSE |
|---|---|---|---|
| 2.3.0 | pass | pass (401 without the bearer token, full session with it, `DELETE` on close) | pass |
| 1.30.0 | pass | pass (headers via `httpx.AsyncClient`) | covered by 2.3.0 only |

Each run also checks `close()` is idempotent and leaves no loop/thread.
A tool call, a resource read and a prompt render were exercised in every mode.

**CLI/REPL smoke** (SDK not installed in the project venv):

* `python main.py mcp list --mcp-config <http server>` → actionable "the 'mcp'
  package is not installed" block, no traceback.
* `python main.py mcp list --mcp-config missing.json` → exit 1 with
  `[MCP error] mcp.json — config`.
* `python main.py mcp list` (no config) → `[MCP] No MCP servers configured`.
* `chat` + `/mcp`, `/mcp reconnect`, `/mcp reconnect <unknown>` → "MCP is
  disabled" messages, no traceback.

Not reproducible in this environment: the **older** 1.x dialect
(`streamablehttp_client` + `headers=` + 3-tuple streams) was verified from the
installed 1.9.4 source and by fake-SDK unit tests, but not executed live —
`mcp` 1.9.4 cannot import with `pydantic>=2.11`, and `pydantic` 2.10 has no
Python 3.14 wheel here.

## 5. Remaining (optional, not started)

- Per-server `enabled: false` flag (today you delete the entry).
- `mcp list --agent <name>` to show exactly what that agent would get.
- Interactive OAuth for remote servers (v2 `Client` + OAuth provider); today
  only static `headers` with `${ENV}` tokens.
- Caching `tools/list` across reconnects (currently re-listed on every connect).
- Pagination for `list_resources` / `list_prompts` (they fetch one page; the
  synthetic tools are read-only views, so this is cosmetic until a server
  paginates).
- Phase 3 candidates: sampling/elicitation callbacks, progress notifications,
  per-tool rate limits.

## 6. Files touched in phase 2 (+ verification follow-up)

```
 M main.py
 M cli/repl.py
 M readme.md
 M mcp.example.json
 M utils/mcp/__init__.py
 M utils/mcp/adapter.py
 M utils/mcp/config.py
 M utils/mcp/connection.py
 M utils/mcp/errors.py          (render(): duplicate header fix)
 M utils/mcp/manager.py
 M tests/unit/test_mcp_config.py       (+3 render tests)
 M tests/unit/test_mcp_connection.py   (+11: pagination, http dialect, hints)
 M tests/unit/test_mcp_manager.py
 M tests/unit/test_repl.py
 M tests/unit/test_main.py
?? tests/manual/mcp/            (real-SDK smoke harness + README)
```
