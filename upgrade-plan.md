**TL;DR**
We're adding a permission gate in front of tool execution plus an interactive REPL to host the prompts, with persistence deliberately deferred (rules live in memory for the process lifetime). The gate sits in the main thread *before* the concurrent tool executor, and a denial is synthesized as a `role: "tool"` result carrying the user's message — never as a `user` message. Grading is per-agent: the policy is constructed with the agent's actual tool set so the fail-closed default only fires for tools the agent exposes. The REPL binds to a session on the first turn and stays bound across turns until the user explicitly starts a new one. The single biggest risk is orphaned tool calls in persisted sessions when a run dies mid-prompt, which must be recovered and **persisted** on load or resumed sessions will 400.
---

## Project Classification
Internal tool / local utility, feature addition to an existing codebase. Not a production service. No multi-user, no network surface, no tenancy. Optimize for correctness of the agent loop and developer ergonomics, not scale.

## Goal
Introduce a permission layer that grades tools as safe or permission-required, prompts the user interactively before executing gated tools, and lets a denial carry a free-text instruction back to the model. Host the prompts in a persistent interactive REPL so the user isn't restarting the process per turn. Persistence of rules is out of scope for this iteration.

## Confirmed Requirements
1. Tools are graded: **safe** (execute silently) vs **ask** (prompt first). Grading is **per-agent**: the policy is constructed with the agent's actual tool names.
2. On a gated tool, the user can: allow once, allow always (in-memory), deny once, deny always (in-memory), or **deny with a message**.
3. A denial-with-message must reach the model as a valid tool result so it can re-plan.
4. An interactive REPL hosts the prompts and removes per-turn process restart. The REPL **binds to a session on the first turn and stays bound** across all subsequent turns until the user explicitly starts a new one (`/new`).
5. Persistence of rules is **on hold** — rules are in-memory only.

## Assumptions
- Single user, local machine, interactive terminal. *(If invalidated — e.g. headless/CI use — the fail-closed default becomes critical.)*
- The existing one-shot `run` path must keep working for scripts. *(If invalidated, we can make `ask` the universal default.)*
- "Allow always" scoped to the process lifetime is acceptable for now. *(If invalidated, we pull the persistence phase forward — the interface is designed for it.)*
- The model is OpenAI-compatible and honors the tool-result contract. *(Already true for the whole project.)*

## Constraints
- No new heavy dependencies. `prompt_toolkit`/`rich` are optional; plain `input()` must be sufficient for MVP.
- Must not break the existing pytest suite — tests must be able to inject a non-interactive resolver.
- The concurrent tool executor (`ThreadPoolExecutor`, up to 8 workers) must remain untouched; the gate runs before it.
- Streaming currently writes directly to stdout via `print()` in `utils/llm_client.py`; the REPL must tolerate this until/unless the sink refactor lands.

## Recommended Stack
- **No new runtime deps for MVP.** `input()` + `print()` for the prompt and REPL.
- Optional later: `rich` for prompt rendering and turn separation (degrades gracefully when piped). `prompt_toolkit` only if line editing/history becomes a real pain.
- Reuse existing SQLite only for session history (unchanged). No new tables this iteration.

## Architecture Overview
Three layers, cleanly separated so the UI is a consumer, not a hack:

```
┌─────────────────────────────────────────────┐
│  CLI layer (cli/)                            │
│  REPL loop · permission prompt · resolver    │
└───────────────┬─────────────────────────────┘
                │ injects PermissionResolver
┌───────────────▼─────────────────────────────┐
│  Permission layer (core/permissions.py)      │
│  grading · policy · decision types · store   │
└───────────────┬─────────────────────────────┘
                │ called from main thread
┌───────────────▼─────────────────────────────┐
│  Agent loop (core/base_agent.py)             │
│  gate → execute_tool_calls → merge results   │
└─────────────────────────────────────────────┘
```

The permission layer knows nothing about the terminal. The CLI layer knows nothing about the agent loop. `base_agent` depends only on the `PermissionResolver` protocol.

## Component Breakdown

**`core/permissions.py`** — the heart. Contains:
- `Decision` enum: `ALLOW_ONCE`, `ALLOW_ALWAYS`, `DENY_ONCE`, `DENY_ALWAYS`, `DENY_WITH_MESSAGE`.
- `PermissionRequest` dataclass: `tool_name`, `args`, `tool_call_id`.
- `PermissionOutcome` dataclass: `allowed: bool`, `message: str | None`, `decision: Decision`. The `decision` field lets the synthesis layer distinguish `DENY_ONCE` from `DENY_ALWAYS` from `DENY_WITH_MESSAGE` and produce the correct result text. (`DENY_ONCE` and `DENY_ALWAYS` produce identical result text — only `DENY_WITH_MESSAGE` differs — but carrying the decision removes ambiguity at the synthesis site.)
- `PermissionResolver` protocol: `resolve(request) -> PermissionOutcome`.
- `PermissionPolicy`: constructed with the **agent's actual tool names** plus the grading map + rule store. Exposes `check(tool_name, args) -> PermissionOutcome`. This is what `base_agent` calls.
- Grading map: `SAFE_TOOLS` set (global constant). Grading resolution is **per-agent** (see Permission Model below).

**`core/rule_store.py`** — `RuleStore` protocol (`get`/`set` keyed by `(scope, scope_key, tool_name)`) plus an `InMemoryRuleStore` implementation. The protocol exists now purely so the SQLite implementation drops in later without touching callers. **For v1, scope is hardcoded to `"session"` and `scope_key` is the session ID** (exposed via `self.session_id`). This gives `ALLOW_ALWAYS`/`DENY_ALWAYS` a concrete key to write without implementing the full scope system.

**`cli/resolver.py`** — `CliPermissionResolver`, the interactive implementation of `PermissionResolver`. Renders the prompt, reads the choice, returns an outcome. Also `AutoAllowResolver` and `AutoDenyResolver` for tests and non-interactive modes.

**`cli/prompt.py`** — pure rendering: formats a `PermissionRequest` into the human-readable prompt (pretty-printed args, truncated long values, scope labels on the "always" options).

**`cli/repl.py`** — the interactive loop. Resolves provider/model/session once, holds the agent alive, dispatches meta-commands, handles `KeyboardInterrupt` per turn. Calls `agent.run(query)` per turn; session binding is handled inside `run()` (see Session Binding Contract).

**`core/base_agent.py`** — modified: accepts an injected `PermissionPolicy` (constructed with `self.tool_map` keys), adds the resolve-before-execute gate, synthesizes denial results, merges in original order, recovers and **persists** orphaned tool calls on load, and exposes `self.session_id`. `run()` binds to a session on the first call and reuses it on subsequent calls.

**`utils/llm_client.py`** — optionally refactored to accept a stream sink. Not required for the permission feature; required only for clean REPL rendering. Deferred to a late phase.

## Permission Model

**Grading (static per-tool constant, applied per-agent, v1):**

| Class | Tools |
|---|---|
| SAFE | `get_current_date`, `glob_search`, `grep_search`, `get_all_files`, `read_lines`, `file_read`, `read_image`, `tree_sitter_tags`, `web_search`, `request_get`, `finish` |
| ASK | `file_write`, `file_edit`, `remove_lines`, `replace_lines`, `file_delete`, `run_command` |

`SAFE_TOOLS` is a global constant set in `core/permissions.py`. Grading lives in the policy layer, **not** in `tool_definition.json`. The wire format the model sees stays untouched.

**Per-agent grading resolution (the rule that prevents the fail-closed default from misfiring):**

The `PermissionPolicy` is constructed with the agent's actual tool names (the keys of `self.tool_map`). Grading resolves as:

```
if tool_name in agent_tool_names and tool_name in SAFE_TOOLS → ALLOW (safe)
elif tool_name in agent_tool_names                          → ASK (gated)
else  # tool_name not in agent_tool_names                   → hallucinated tool
    synthesize an ERROR result (not a permission prompt):
    "Error: unknown tool '{tool_name}' — not registered for this agent"
```

This is critical: "unknown" means "not in this agent's tool set," **not** "not in SAFE_TOOLS." Without the agent's tool names, the fail-closed default would prompt (or deny in non-interactive mode) for tools the agent doesn't even have — e.g. the `default` agent (which only exposes `get_current_date`) would treat `web_search` as unknown and prompt the user for a tool that can't run. Conversely, a hallucinated tool name that happens to be in `SAFE_TOOLS` would be auto-allowed without the agent having it registered — the `execute_tool_call` layer would catch it as a `KeyError`, but the policy should fail fast with a clear error result instead.

Note: `tree_sitter_tags` is defined inline in `agents/code/tools.py`, not in `tool_definition.json`. The grading map is a hardcoded Python set, not derived from the JSON, so it correctly includes `tree_sitter_tags`. If a new inline tool is added to an agent's `tools.py` without updating `SAFE_TOOLS`, it defaults to ASK (fail-closed) — this is the intended, safe behavior and should be documented in the agent authoring guide.

**Decision semantics:**

| Decision | Executes? | Persists (in-memory)? | Carries message? | Meaningful in one-shot `run`? |
|---|---|---|---|---|
| allow once | yes | no | no | yes |
| allow always | yes | yes (process) | no | no (process exits immediately) — behaves as allow once |
| deny once | no | no | no | yes |
| deny always | no | yes (process) | no | no (process exits immediately) — behaves as deny once |
| deny with message | no | **no** | yes | yes |

`deny with message` is a one-time redirect, not a standing rule — it must never be written to the rule store.

`allow always` and `deny always` are only meaningful in the REPL, where the process persists across turns. In one-shot `run` mode, the process exits after the single `run()` call, so "always" is indistinguishable from "once." This is documented behavior, not a bug: `allow always` in one-shot mode behaves as `allow once`, and `deny always` behaves as `deny once`. Users who test with `run` will see this; the feature is live in the REPL.

**The denial result (exact shape):** a `role: "tool"` message with the original `tool_call_id` and content:

```
Permission denied by user. The user says: "<message>". Do not retry this tool unless explicitly asked.
```

When there is no message, use: `Permission denied by user. Do not retry this tool unless explicitly asked.` The "do not retry" framing is load-bearing — without it the model re-issues the same call.

**Resolver interface (the seam that keeps tests green):**

```python
class PermissionResolver(Protocol):
    def resolve(self, request: PermissionRequest) -> PermissionOutcome: ...
```

`base_agent` never calls `input()`. It calls `policy.check(...)`, which consults the rule store, and if unresolved, delegates to the injected resolver. Tests inject `AutoAllowResolver`/`AutoDenyResolver`.

## Session Binding Contract

The REPL must bind to a session on the first turn and stay bound across all subsequent turns until the user explicitly starts a new one. The current `run()` design fights this: `session_id` is a local variable resolved via `init_session_db(self.resume_session, ...)` on every call (line 125). Without a binding contract, every REPL turn after the first creates a new, empty session and the conversation history is lost.

**The binding contract (modifications to `base_agent.run()`):**

1. **On entry, check for an existing binding:**
   ```python
   if self.session_id is not None:
       # Already bound — resume the existing session, skip init_session_db
       session_id = self.session_id
   else:
       # First call (or after /new reset) — create or resume
       session_id = init_session_db(
           self.resume_session, self.session_name, self.system_prompt,
           agent_name=agent_dir,
       )
       self.session_id = session_id   # BIND
   ```

2. **`/new` meta-command resets the binding:**
   ```python
   self.session_id = None
   self.resume_session = None
   ```
   The next `run()` call creates a fresh session and re-binds.

3. **`chat --continue` resolves the latest session ID once at startup** (reusing `do_run`'s `--continue` logic, lines 53–64 of `main.py`), passes it as `resume_session` to the agent constructor. The first `run()` call resumes it; the binding keeps it stable across all subsequent turns.

4. **`chat --resume-session <id>` works identically** — bind once at startup, persist across turns.

5. **`/session` meta-command is read-only** in v1: prints the current bound session ID and name. Mid-REPL session switching is deferred (it would need the same `self.session_id` reset that `/new` uses, plus a target ID — out of scope for MVP).

This contract means the REPL is a loop around the existing one-shot `run()` path with a different input source. Session state is persisted via the existing DB path; in-memory permission rules survive across turns because they live on the `PermissionPolicy` instance, not in `run()` locals. No new `run()` method or refactor is needed — just the binding check at the top of `run()`.

## Data Flow

```
model returns assistant message with tool_calls
  → persist assistant message (existing, line 204)
  → GATE (main thread, sequential):
       for each tool_call:
         outcome = policy.check(fn_name, fn_args)
           ├─ tool not in agent_tool_names → synthesize ERROR result (hallucinated tool)
           ├─ SAFE or rule hit → allowed
           └─ ASK → resolver.resolve() → prompt user
       approved → execute_tool_calls(approved)   # concurrent, unchanged
       denied   → synthesize tool result
  → merge approved + denied + error results in ORIGINAL tool_call order
  → append tool messages + persist (existing, lines 216–231)
  → finish detection (existing, line 233)
  → next turn
```

Order preservation is mandatory: the OpenAI contract requires tool results to correspond to the assistant's tool calls, and the loop already relies on ordered results.

## Suggested Project Structure

```
core/
  permissions.py      # NEW — policy, grading, decision types, resolver protocol
  rule_store.py       # NEW — RuleStore protocol + InMemoryRuleStore
  base_agent.py       # MODIFIED — gate, orphan recovery+persist, session binding, session_id exposure
cli/
  __init__.py         # NEW
  repl.py             # NEW — interactive loop + meta-commands
  resolver.py         # NEW — CliPermissionResolver, AutoAllow/AutoDeny
  prompt.py           # NEW — prompt rendering
  sink.py             # NEW (late phase) — streaming sink
main.py               # MODIFIED — `chat` subcommand / --interactive, --permission-mode, --continue, --resume-session
```

## Failure Analysis & Mitigations

1. **Orphaned tool calls on crash mid-prompt.** The assistant message with `tool_calls` is persisted *before* the gate. If the process dies while prompting, the session has tool calls with no matching results → next request 400s. **Mitigation:** on session load in `base_agent.run()` (around line 135), scan for assistant messages whose `tool_calls` lack corresponding `tool` messages and synthesize abort results for them — **and persist those results to the DB**. This is a write-on-load operation that heals the persisted state, so a crashed session is fixed once and stays fixed. Detection logic: (a) scan all assistant messages with `tool_calls`, (b) collect all `tool_call_id`s from those, (c) subtract `tool_call_id`s already present in `tool`-role messages, (d) for each unmatched `tool_call_id`, synthesize AND persist an abort `tool` message. Cheap, and you *will* hit this.

2. **Prompting from worker threads.** The executor fans out to 8 threads; prompting there is chaos. **Mitigation:** the gate runs strictly in the main thread before dispatch. Non-negotiable.

3. **Non-interactive / piped / CI runs.** No one to prompt. **Mitigation:** fail-closed (deny) when stdin is not a TTY, with an explicit `--yes` / `--permission-mode allow` escape hatch. Never silently allow because stdin isn't a TTY.

4. **Prompt blocks forever.** The 300s per-call timeout is *after* the gate. **Mitigation:** block indefinitely in interactive mode (the user is present) — a deliberate choice, documented, not an accident.

5. **Multi-tool turns = prompt fatigue.** Five gated tools → five prompts. **Mitigation:** deferred to Future Enhancements (an "allow all in this batch" option). Not in v1 requirements or the `Decision` enum. If prompt fatigue is a real problem in testing, add `ALLOW_BATCH` to the enum and requirement #2 in a follow-up.

6. **`shutdown()` called per turn kills the REPL.** `shutdown()` sets `_shutdown_requested = True` permanently and tears down the global thread pool + HTTP session. **Mitigation:** call it only on REPL exit, never between turns.

7. **`KeyboardInterrupt` kills the process.** `do_run` currently `sys.exit(0)`s. **Mitigation:** catch per turn in the REPL, return to the prompt, leave the session resumable.

8. **Denial framed as a tool error.** The model retries. **Mitigation:** the explicit "do not retry unless explicitly asked" wording.

9. **`max-turns` reached mid-REPL.** One-shot mode ends the run. **Mitigation:** in the REPL, hitting the cap returns control to the prompt with the session resumable.

10. **Model re-issues a denied tool call in the next turn.** The "do not retry" wording is the primary mitigation, but models sometimes ignore it. **Mitigation:** if `DENY_ONCE` was chosen and the model re-issues the identical call (same `tool_name` + same `args`) in the immediately following turn, auto-deny without re-prompting. (Soft mitigation; hard guard is `DENY_ALWAYS` via the rule store.) Accepted as a known v1 limitation if the model is stubborn.

11. **`finish` called mid-batch with a side-effecting tool.** If `finish` (SAFE) and `file_write` (ASK, approved) are in the same batch, `file_write` executes concurrently with `finish`. The `finish` detection (line 233) happens after all results merge, so the side effect completes. **Mitigation:** acceptable for v1 (the existing code already has this behavior). Note as a known limitation.

12. **`finish` in REPL mode.** `finish` is a SAFE tool that returns a string; it does not call `shutdown()`. **Mitigation:** in REPL mode, `finish` ends the current `run()` call and returns control to the REPL prompt — it does not exit the process. `shutdown()` fires only on `/quit` or EOF. Specify this explicitly in Phase 4.

## Development Phases

**Phase 1 — Permission core (no UI).** Objective: pure, testable policy engine.
Deliverables: `core/permissions.py`, `core/rule_store.py`, grading map (`SAFE_TOOLS` constant), per-agent grading resolution, decision types, `PermissionOutcome` with `decision` field, resolver protocol, `AutoAllow`/`AutoDeny` resolvers. Scope hardcoded to `"session"`, `scope_key` to session ID.
Effort: 1–1.5 person-days. Team: 1 dev.

**Phase 2 — Gate integration.** Objective: wire the policy into the loop.
Deliverables: `base_agent` gate before `execute_tool_calls`, per-agent policy construction (with `self.tool_map` keys), hallucinated-tool error results, denial synthesis, ordered merge, orphan recovery + persist on load, `self.session_id` exposure, session binding contract in `run()`.
Effort: 1.5 person-days. Team: 1 dev. Depends on Phase 1.

**Phase 3 — CLI resolver + prompt.** Objective: the interactive prompt.
Deliverables: `cli/resolver.py`, `cli/prompt.py`, `--permission-mode` flag, TTY detection + fail-closed default.
Effort: 1 person-day. Team: 1 dev. Depends on Phase 2.

**Phase 4 — REPL.** Objective: persistent interactive loop.
Deliverables: `cli/repl.py`, `chat` subcommand / `--interactive`, `--continue` / `--resume-session` wiring, meta-commands (`/quit`, `/new`, `/session`, `/agents`, `/provider`, `/model`, `/help`), per-turn `KeyboardInterrupt`, shutdown-on-exit, `finish`-returns-to-prompt behavior. (`/permissions` deferred to persistence phase.)
Effort: 3 person-days. Team: 1 dev. Depends on Phase 3.

**Phase 5 — Streaming sink + polish (optional).** Objective: clean REPL rendering.
Deliverables: `cli/sink.py`, `llm_client` sink parameter with default print sink, turn separators.
Effort: 1 person-day. Team: 1 dev. Depends on Phase 4.

**Phase 6 — Tests + hardening.** Objective: lock behavior.
Deliverables: unit tests for policy/decisions/merge/orphan recovery; integration test for a deny-with-message round trip; REPL smoke test; session binding test (second turn reuses first turn's session).
Effort: 1–1.5 person-days. Team: 1 dev. Runs alongside Phases 2–5.

**Total: ~8–10 person-days, solo.**

## Critical Path
Phase 1 → Phase 2 → Phase 3 → Phase 4. Phase 5 is optional and off the critical path. Phase 6 runs continuously. The permission engine (1–2) is the hard, testable core; the UI (3–4) is justified by needing to talk to a human, not a separate ambition.

## Testing Strategy
- **Unit:** grading resolution (per-agent: safe, ask, hallucinated), each decision type, rule-store hit/miss, ordered merge of approved+denied+error results, orphan recovery + persist.
- **Integration:** a scripted resolver that denies with a message → assert the persisted tool message carries the right `tool_call_id` and content, and that the loop continues.
- **Contract:** assert every assistant `tool_calls` entry has a matching `tool` result after a turn, including denials and hallucinated-tool errors.
- **Session binding:** call `run()` twice on the same agent instance → assert the second call reuses the first call's `session_id` (no new session created).
- **Orphan recovery (write-first test):** create a session with an assistant message containing `tool_calls` and no matching `tool` rows → call load → assert `tool` rows now exist with abort content in the DB.
- **REPL:** smoke test with piped input; assert `KeyboardInterrupt` returns to prompt and `shutdown()` fires once on exit; assert `finish` returns to prompt, not exit.
- **Regression:** existing suite must pass with `AutoAllowResolver` injected by default in `conftest.py`.

## Resolved Decisions

- **`--permission-mode` default (resolved, not deferred):** One-shot `run` defaults to `allow` (preserving backward compat for existing scripts). The new `chat` / `--interactive` REPL defaults to `ask`. This keeps scripts working and makes the REPL the safe-by-default path. `--yes` / `--permission-mode allow` is the explicit escape hatch for non-interactive use; fail-closed (deny) when stdin is not a TTY and no escape hatch is given.

- **Per-agent grading (resolved):** `PermissionPolicy` is constructed with the agent's actual tool names (`self.tool_map` keys). Grading resolution: `SAFE ∩ agent_tools → allow`; `agent_tools \ SAFE → ask`; hallucinated (not in agent_tools) → error result. The fail-closed default only fires for tools the agent exposes.

- **`PermissionOutcome` shape (resolved):** Carries `allowed: bool`, `message: str | None`, `decision: Decision`. The `decision` field removes ambiguity at the synthesis site. `DENY_ONCE` and `DENY_ALWAYS` produce identical result text; only `DENY_WITH_MESSAGE` differs.

- **RuleStore scope (resolved):** v1 hardcodes scope to `"session"` and `scope_key` to `self.session_id`. The full scope system (`session` / `project` / `global`) is deferred.

- **"Allow all in this batch" (resolved):** Deferred to Future Enhancements. Not in v1 requirements or the `Decision` enum.

- **`/permissions` meta-command (resolved):** Deferred to the persistence phase. Low value for v1 since in-memory rules reset on restart.

## Risks & Challenges
- **`run_command` is coarse.** `ls` and `rm -rf /` grade identically. Accepted for v1; the rule interface must be able to see `fn_args` so argument-level rules slot in later without a rewrite.
- **Streaming coupling.** Until Phase 5, REPL output is whatever `print()` emits. Acceptable, but the sink refactor is the clean fix.
- **In-memory rules reset on restart.** Expected given persistence is on hold; the `RuleStore` protocol ensures the swap is mechanical.
- **Phase 4 estimate.** Seven meta-commands plus agent re-instantiation mid-REPL (`/agents`, `/provider`, `/model`) is careful work. Estimated at 3 person-days, not 2.

## MVP Scope
Phases 1–4. Per-agent static grading, in-memory rules (scope hardcoded to session), interactive prompt with all five decisions, deny-with-message round trip, hallucinated-tool error results, REPL with core meta-commands, session binding across turns, orphan recovery + persist, fail-closed non-interactive default, `run` defaults to `allow` / `chat` defaults to `ask`. No persistence, no argument-level rules, no "allow all in batch", no `/permissions` UI, no sink refactor.

## Future Enhancements
- **Persistence (deferred):** SQLite `tool_permissions` table keyed by `(scope, scope_key, tool_name)`; scopes `session` / `project` (`FILES_BASE_DIR`) / `global`; swap `InMemoryRuleStore` for a SQLite implementation behind the existing protocol.
- **Argument-level rules** for `run_command` (pattern matching on the command string).
- **`/permissions` management UI** to view and revoke rules.
- **"Allow all in this batch"** option (`ALLOW_BATCH` in the `Decision` enum) to reduce prompt fatigue on multi-tool turns.
- **Mid-REPL session switching** via `/session <id>` (needs the same `self.session_id` reset as `/new` plus a target ID).
- **Streaming sink** for richer rendering (Phase 5).
- **`rich`/`prompt_toolkit`** upgrade for prompt rendering and line editing.
