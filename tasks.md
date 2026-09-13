# Task List — Permission Gate + Interactive REPL

Derived from `upgrade-plan.md`. Line anchors reference the current code and are verified.
MVP scope = Phases 1–4 (+ Phase 6 continuous). Phase 5 is optional/off critical path.

**Non-negotiables (apply to every task below):**
- The permission gate runs in the **main thread**, strictly before `execute_tool_calls` (the ThreadPoolExecutor, ≤8 workers, stays untouched).
- Tool results (approved, denied, hallucinated-error) are merged in **original tool_call order** — OpenAI contract requires 1:1 correspondence with the assistant's `tool_calls`.
- A denial is synthesized as a `role: "tool"` message with the original `tool_call_id` — never as a `user` message.
- `shutdown()` (core/base_agent.py:243) fires **once on REPL exit only**, never between turns.
- No new runtime dependencies; plain `input()`/`print()` for MVP.
- Persistence of permission rules is out of scope — in-memory only.

---

## Phase 1 — Permission core (no UI). Pure, testable policy engine.

- [x] **T1. Create `core/permissions.py` — types + protocol.**
  - `Decision` enum: `ALLOW_ONCE`, `ALLOW_ALWAYS`, `DENY_ONCE`, `DENY_ALWAYS`, `DENY_WITH_MESSAGE`.
  - `PermissionRequest` dataclass: `tool_name`, `args`, `tool_call_id`.
  - `PermissionOutcome` dataclass: `allowed: bool`, `message: str | None`, `decision: Decision`.
  - `PermissionResolver` protocol: `resolve(request) -> PermissionOutcome`.
  - `SAFE_TOOLS` global constant set: `get_current_date, glob_search, grep_search, get_all_files, read_lines, file_read, read_image, tree_sitter_tags, web_search, request_get, finish`.
  - Note: `tree_sitter_tags` is defined inline in `agents/code/tools.py`, not in `utils/tool_definition.json` — the set is hardcoded Python, so include it explicitly.
- [x] **T2. Create `core/rule_store.py`.**
  - `RuleStore` protocol: `get`/`set` keyed by `(scope, scope_key, tool_name)`.
  - `InMemoryRuleStore` implementation. v1: scope hardcoded `"session"`, `scope_key` = session ID.
  - Protocol exists so a SQLite store drops in later without touching callers.
- [x] **T3. Implement `PermissionPolicy` with per-agent grading resolution.**
  - Constructor takes: the **agent's actual tool names** (keys of `self.tool_map`), the grading map, the rule store, and a resolver.
  - `check(tool_name, args) -> PermissionOutcome` resolves as:
    - `tool_name in agent_tool_names and tool_name in SAFE_TOOLS` → allow (safe, no prompt, no rule write).
    - `tool_name in agent_tool_names` (not safe) → consult rule store; on hit honor it; on miss delegate to resolver; write store **only** for `ALLOW_ALWAYS`/`DENY_ALWAYS`.
    - `tool_name not in agent_tool_names` → hallucinated tool: produce an **error** outcome (not a prompt): `"Error: unknown tool '{tool_name}' — not registered for this agent"`.
  - `DENY_WITH_MESSAGE` must never be written to the rule store (one-time redirect).
  - Rationale (from plan): "unknown" means *not in this agent's tool set*, not *not in SAFE_TOOLS*. The `default` agent exposes only `get_current_date`; without per-agent grading the fail-closed default would prompt for tools that can't run.
- [x] **T4. Add test/non-interactive resolvers** in `core/permissions.py`: `AutoAllowResolver`, `AutoDenyResolver`.
- [x] **T5. Unit tests `tests/unit/test_permissions.py`.**
  - Grading resolution per-agent: safe, ask, hallucinated (all three branches).
  - Each of the five decision types; rule-store hit/miss; `DENY_WITH_MESSAGE` never persisted; `ALLOW_ALWAYS`/`DENY_ALWAYS` persisted with session scope key.
  - `AutoAllow`/`AutoDeny` behave as documented.

## Phase 2 — Gate integration. Wire the policy into the loop.

- [x] **T6. `core/base_agent.py` constructor (lines 20–46): accept + construct the policy.**
  - Accept an optional `permission_policy` / permission-mode argument (tests inject their own).
  - After `self.tool_map = self.get_tool_map()` (line 45), construct `PermissionPolicy` from `self.tool_map.keys()` with an `InMemoryRuleStore` if none injected.
  - Default resolver for one-shot backward compat: `AutoAllowResolver` (existing scripts must keep working).
  - Add `self.session_id: int | None = None` (exposed for rule-store scope keys and the REPL).
- [x] **T7. Session binding contract in `run()` (session init at lines 124–130).**
  - On entry: if `self.session_id` is not None → reuse it, skip `init_session_db`. Else create/resume via `init_session_db(self.resume_session, self.session_name, self.system_prompt, agent_name=agent_dir)` and **bind** `self.session_id = session_id`.
  - `/new` (Phase 4) resets `self.session_id = None; self.resume_session = None` → next `run()` re-binds to a fresh session.
  - `--continue` / `--resume-session` resolve the latest/given session ID once at startup, pass as `resume_session`; first `run()` resumes, binding keeps it stable.
- [x] **T8. Orphaned tool-call recovery on load — insert after `get_messages` (line 135).** **Highest-risk item in the plan.**
  - Detect: collect all `tool_call_id`s from assistant messages carrying `tool_calls`; subtract ids already answered by `role: "tool"` messages.
  - For each unmatched id: synthesize an abort `tool` result AND **persist it via `session_db.add_message(session_id, "tool", ..., tool_call_id=id)`** (write-on-load heals the DB once; resumed sessions otherwise 400).
- [x] **T9. Permission gate before `execute_tool_calls` (insert between lines 204 and 209).**
  - Sequential, main-thread loop over `tool_calls`: `policy.check(fn_name, fn_args)` per call.
  - Partition into approved / denied / error buckets. Approved list goes to `execute_tool_calls(tool_calls_approved, ...)` unchanged. Denied/errored calls never reach the executor.
- [x] **T10. Synthesis + ordered merge.**
  - Produce results as `(tc, fn_name, fn_args, result_str)` tuples matching `execute_tool_calls`' shape so the existing merge loop (lines 216–231) and `finish` detection (line 233) work unmodified.
  - Denial content, exact shape (`role: "tool"`, original `tool_call_id`):
    - With message: `Permission denied by user. The user says: "<message>". Do not retry this tool unless explicitly asked.`
    - Without: `Permission denied by user. Do not retry this tool unless explicitly asked.`
    - The "do not retry" framing is load-bearing — without it the model re-issues the call.
  - Hallucinated tool content: `Error: unknown tool '{tool_name}' — not registered for this agent`.
  - Merge approved + denied + error in **original tool_call order**.
  - `finish` (SAFE) semantics: ends the current `run()` and returns control to the REPL prompt in interactive mode; does not exit the process.
- [x] **T11. Gate tests + conftest regression safety.**
  - `tests/unit/test_base_agent_gate.py`: gate-before-execute ordering, ordered merge of approved/denied/error, denial text shape, orphan recovery **persists** rows (write-first test: seed assistant msg with `tool_calls`, no `tool` rows → load → assert `tool` rows exist in DB), session binding (two `run()` calls on one instance → same `session_id`, no new session created).
  - `tests/conftest.py`: fixture injecting `AutoAllowResolver` so the existing suite passes untouched.

## Phase 3 — CLI resolver + prompt. The interactive prompt.

- [ ] **T12. Create `cli/__init__.py` and `cli/prompt.py` (pure rendering).**
  - Format a `PermissionRequest` into a human-readable prompt: pretty-printed args, truncated long values, scope labels on the "always" options. No I/O in this module.
- [ ] **T13. Create `cli/resolver.py`.**
  - `CliPermissionResolver`: renders via `cli/prompt.py`, reads choice via `input()`; offers allow once / allow always / deny once / deny always / deny with message; loops until a valid choice; returns matching `PermissionOutcome`.
  - Non-interactive safety: **fail-closed (deny)** when stdin is not a TTY. Never silently allow because stdin isn't a TTY.
  - Factory for `--permission-mode` values (`allow` → AutoAllow, `deny`/default-non-tty → AutoDeny, `ask` → Cli).
- [ ] **T14. `--permission-mode` flag + defaults (resolved decision).**
  - One-shot `run` defaults to `allow` (backward compat for scripts); new `chat`/`--interactive` REPL defaults to `ask`.
  - `--yes` / `--permission-mode allow` is the explicit escape hatch for non-interactive use.
  - Prompt blocking is indefinite in interactive mode (user is present) — documented, deliberate; the 300s per-call timeout is *after* the gate.

## Phase 4 — REPL. Persistent interactive loop.

- [ ] **T15. `main.py`: `chat` subcommand / `--interactive`.**
  - Add subparser in `build_parser()`; register in `main()` argv dispatch (line 283: `("run", "session", "chat")`).
  - Reuse `do_run`'s `--continue` / `--resume-session` resolution (lines 51–64) and provider/model resolution; pass `resume_session` into the agent constructor.
  - **Do not** copy `do_run`'s `finally: agent.shutdown()` (lines 123–124) — the agent must stay alive across turns.
- [ ] **T16. Create `cli/repl.py` — the loop.**
  - Resolve provider/model/session once; hold one agent instance for the process lifetime.
  - Per turn: read input → `agent.run(query)`. Catch `KeyboardInterrupt` per turn → return to prompt, session stays resumable (do not `sys.exit`).
  - Hitting `max-turns` returns control to the prompt with the session resumable (one-shot behavior already ends the run).
  - `shutdown()` called only on `/quit` or EOF, never between turns (plan failure #6).
  - `finish` (SAFE tool) ends the current `run()` and returns to the prompt; process keeps running.
  - Streaming still `print()`s from `utils/llm_client.py` — tolerate until Phase 5.
- [ ] **T17. Meta-commands.**
  - `/quit` (shutdown + exit), `/new` (reset `session_id`/`resume_session` binding → next turn re-binds fresh), `/session` (read-only v1: print bound session ID + name), `/agents`, `/provider`, `/model` (re-instantiate agent; preserve binding semantics), `/help`.
  - `/permissions` deferred to the persistence phase (rules reset on restart anyway).
  - Note: because rules are keyed by session scope, `/new` naturally scopes out old allow/deny-always rules.
- [ ] **T18. REPL smoke test** with piped input: prompt appears, one turn executes via mocked agent, `KeyboardInterrupt` returns to prompt, `shutdown()` fires exactly once on `/quit`/EOF, `finish` returns to prompt not exit.

## Phase 5 — Streaming sink + polish (optional, off critical path)

- [ ] **T19. `cli/sink.py` + `llm_client` sink parameter.**
  - Optional stream-sink param on `utils/llm_client.py` streaming path (defaults to current `print()` behavior so one-shot output is unchanged); REPL passes a sink for clean turn separation. Only needed for clean REPL rendering.

## Phase 6 — Tests + hardening (runs alongside Phases 2–5)

- [ ] **T20. Integration test: deny-with-message round trip** (`tests/integration/test_permission_flow.py`).
  - Scripted resolver denies with a message → assert the persisted `tool` message carries the original `tool_call_id` and the exact denial content, and the loop continues (model can re-plan).
- [ ] **T21. Contract test:** after every turn — including denials and hallucinated tools — every assistant `tool_calls` entry has a matching `tool` result, in order.
- [ ] **T22. Full regression:** existing pytest suite green. `run` unchanged by default (AutoAllow), REPL path fail-closed under `ask` with non-TTY stdin.
- [ ] **T23. Document:** in `readme.md` (agent authoring guide): new inline tools added to an agent's `tools.py` without updating `SAFE_TOOLS` default to ASK (fail-closed, intended); "always" decisions in one-shot `run` behave as "once" (process exits); `finish` behavior in REPL mode.

---

## Dependency / critical path

```
T0 → T1..T5 (Phase 1) → T6..T11 (Phase 2) → T12..T14 (Phase 3) → T15..T18 (Phase 4)
                                                            ↘ T19 (optional)
T5, T11, T18, T20..T22 = continuous test track
```

## Top risks to watch (from plan failure analysis)

1. **Orphaned tool calls on crash mid-prompt** (T8) — single biggest risk; must recover **and persist** on load.
2. Prompting from worker threads — never; gate is main-thread only (T9).
3. Non-interactive/piped/CI — fail-closed deny + explicit escape hatch (T13/T14).
4. Denial framed as error → model retries — exact "do not retry" wording (T10).
5. `shutdown()` per turn kills REPL — exit-only (T16).
6. Model re-issues identical denied call — accepted v1 limitation; soft guard documented (T23 notes).

**Out of scope (v1):** rule persistence, argument-level rules, "allow all in batch", `/permissions` UI, mid-REPL session switching via `/session <id>`.
