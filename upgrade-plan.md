**TL;DR**
We're adding a permission gate in front of tool execution plus an interactive REPL to host the prompts, with persistence deliberately deferred (rules live in memory for the
 process lifetime). The gate sits in the main thread *before* the concurrent tool executor, and a denial is synthesized as a `role: "tool"` result carrying the user's messa
ge — never as a `user` message. The single biggest risk is orphaned tool calls in persisted sessions when a run dies mid-prompt, which must be recovered on load or resumed
sessions will 400.
---

## Project Classification
Internal tool / local utility, feature addition to an existing codebase. Not a production service. No multi-user, no network surface, no tenancy. Optimize for correctness o
f the agent loop and developer ergonomics, not scale.

## Goal
Introduce a permission layer that grades tools as safe or permission-required, prompts the user interactively before executing gated tools, and lets a denial carry a free-t
ext instruction back to the model. Host the prompts in a persistent interactive REPL so the user isn't restarting the process per turn. Persistence of rules is out of scope
 for this iteration.

## Confirmed Requirements
1. Tools are graded: **safe** (execute silently) vs **ask** (prompt first).
2. On a gated tool, the user can: allow once, allow always (in-memory), deny once, deny always (in-memory), or **deny with a message**.
3. A denial-with-message must reach the model as a valid tool result so it can re-plan.
4. An interactive REPL hosts the prompts and removes per-turn process restart.
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
- `PermissionOutcome` dataclass: `allowed: bool`, `message: str | None`.
- `PermissionResolver` protocol: `resolve(request) -> PermissionOutcome`.
- `PermissionPolicy`: holds the grading map + rule store, exposes `check(tool_name, args) -> PermissionOutcome`. This is what `base_agent` calls.
- Grading map: `SAFE_TOOLS` set; everything else defaults to **ask** (fail-closed for unknown tools).

**`core/rule_store.py`** — `RuleStore` protocol (`get`/`set` keyed by `(scope, scope_key, tool_name)`) plus an `InMemoryRuleStore` implementation. The protocol exists now p
urely so the SQLite implementation drops in later without touching callers.

**`cli/resolver.py`** — `CliPermissionResolver`, the interactive implementation of `PermissionResolver`. Renders the prompt, reads the choice, returns an outcome. Also `Aut
oAllowResolver` and `AutoDenyResolver` for tests and non-interactive modes.

**`cli/prompt.py`** — pure rendering: formats a `PermissionRequest` into the human-readable prompt (pretty-printed args, truncated long values, scope labels on the "always"
 options).

**`cli/repl.py`** — the interactive loop. Resolves provider/model/session once, holds the agent alive, dispatches meta-commands, handles `KeyboardInterrupt` per turn.

**`core/base_agent.py`** — modified: accepts an injected `PermissionPolicy`, adds the resolve-before-execute gate, synthesizes denial results, merges in original order, rec
overs orphaned tool calls on load, and exposes `self.session_id`.

**`utils/llm_client.py`** — optionally refactored to accept a stream sink. Not required for the permission feature; required only for clean REPL rendering. Deferred to a la
te phase.

## Permission Model

**Grading (static, per-tool, v1):**

| Class | Tools |
|---|---|
| SAFE | `get_current_date`, `glob_search`, `grep_search`, `get_all_files`, `read_lines`, `file_read`, `read_image`, `tree_sitter_tags`, `web_search`, `request_get`, `finis
h` |
| ASK | `file_write`, `file_edit`, `remove_lines`, `replace_lines`, `file_delete`, `run_command` |
| Unknown | ASK (fail-closed) |

Grading lives in the policy layer, **not** in `tool_definition.json`. The wire format the model sees stays untouched.

**Decision semantics:**

| Decision | Executes? | Persists (in-memory)? | Carries message? |
|---|---|---|---|
| allow once | yes | no | no |
| allow always | yes | yes (process) | no |
| deny once | no | no | no |
| deny always | no | yes (process) | no |
| deny with message | no | **no** | yes |

`deny with message` is a one-time redirect, not a standing rule — it must never be written to the rule store.

**The denial result (exact shape):** a `role: "tool"` message with the original `tool_call_id` and content:

```
Permission denied by user. The user says: "<message>". Do not retry this tool unless explicitly asked.
```

When there is no message, use: `Permission denied by user. Do not retry this tool unless explicitly asked.` The "do not retry" framing is load-bearing — without it the mode
l re-issues the same call.

**Resolver interface (the seam that keeps tests green):**

```python
class PermissionResolver(Protocol):
    def resolve(self, request: PermissionRequest) -> PermissionOutcome: ...
```

`base_agent` never calls `input()`. It calls `policy.check(...)`, which consults the rule store, and if unresolved, delegates to the injected resolver. Tests inject `AutoAl
lowResolver`/`AutoDenyResolver`.

## Data Flow

```
model returns assistant message with tool_calls
  → persist assistant message (existing, line 204)
  → GATE (main thread, sequential):
       for each tool_call:
         outcome = policy.check(fn_name, fn_args)
           ├─ SAFE or rule hit → allowed
           └─ ASK → resolver.resolve() → prompt user
       approved → execute_tool_calls(approved)   # concurrent, unchanged
       denied   → synthesize tool result
  → merge approved + denied results in ORIGINAL tool_call order
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
  base_agent.py       # MODIFIED — gate, orphan recovery, session_id exposure
cli/
  __init__.py         # NEW
  repl.py             # NEW — interactive loop + meta-commands
  resolver.py         # NEW — CliPermissionResolver, AutoAllow/AutoDeny
  prompt.py           # NEW — prompt rendering
  sink.py             # NEW (late phase) — streaming sink
main.py               # MODIFIED — `chat` subcommand / --interactive, --permission-mode
```

## Failure Analysis & Mitigations

1. **Orphaned tool calls on crash mid-prompt.** The assistant message with `tool_calls` is persisted *before* the gate. If the process dies while prompting, the session has
 tool calls with no matching results → next request 400s. **Mitigation:** on session load in `base_agent.run()` (around line 135), scan for assistant messages whose `tool_c
alls` lack corresponding `tool` messages and synthesize abort results for them. Cheap, and you *will* hit this.

2. **Prompting from worker threads.** The executor fans out to 8 threads; prompting there is chaos. **Mitigation:** the gate runs strictly in the main thread before dispatc
h. Non-negotiable.

3. **Non-interactive / piped / CI runs.** No one to prompt. **Mitigation:** fail-closed (deny) when stdin is not a TTY, with an explicit `--yes` / `--permission-mode allow`
 escape hatch. Never silently allow because stdin isn't a TTY.

4. **Prompt blocks forever.** The 300s per-call timeout is *after* the gate. **Mitigation:** block indefinitely in interactive mode (the user is present) — a deliberate cho
ice, documented, not an accident.

5. **Multi-tool turns = prompt fatigue.** Five gated tools → five prompts. **Mitigation:** a "allow all in this batch" option that approves the remaining gated calls in the
 current turn only.

6. **`shutdown()` called per turn kills the REPL.** `shutdown()` sets `_shutdown_requested = True` permanently and tears down the global thread pool + HTTP session. **Mitig
ation:** call it only on REPL exit, never between turns.

7. **`KeyboardInterrupt` kills the process.** `do_run` currently `sys.exit(0)`s. **Mitigation:** catch per turn in the REPL, return to the prompt, leave the session resumab
le.

8. **Denial framed as a tool error.** The model retries. **Mitigation:** the explicit "do not retry unless explicitly asked" wording.

9. **`max-turns` reached mid-REPL.** One-shot mode ends the run. **Mitigation:** in the REPL, hitting the cap returns control to the prompt with the session resumable.

## Development Phases

**Phase 1 — Permission core (no UI).** Objective: pure, testable policy engine.
Deliverables: `core/permissions.py`, `core/rule_store.py`, grading map, decision types, resolver protocol, `AutoAllow`/`AutoDeny` resolvers.
Effort: 1–1.5 person-days. Team: 1 dev.

**Phase 2 — Gate integration.** Objective: wire the policy into the loop.
Deliverables: `base_agent` gate before `execute_tool_calls`, denial synthesis, ordered merge, orphan recovery on load, `self.session_id` exposure.
Effort: 1 person-day. Team: 1 dev. Depends on Phase 1.

**Phase 3 — CLI resolver + prompt.** Objective: the interactive prompt.
Deliverables: `cli/resolver.py`, `cli/prompt.py`, `--permission-mode` flag, TTY detection + fail-closed default.
Effort: 1 person-day. Team: 1 dev. Depends on Phase 2.

**Phase 4 — REPL.** Objective: persistent interactive loop.
Deliverables: `cli/repl.py`, `chat` subcommand / `--interactive`, meta-commands (`/quit`, `/new`, `/session`, `/agents`, `/provider`, `/model`, `/permissions`, `/help`), pe
r-turn `KeyboardInterrupt`, shutdown-on-exit.
Effort: 1.5–2 person-days. Team: 1 dev. Depends on Phase 3.

**Phase 5 — Streaming sink + polish (optional).** Objective: clean REPL rendering.
Deliverables: `cli/sink.py`, `llm_client` sink parameter with default print sink, turn separators.
Effort: 1 person-day. Team: 1 dev. Depends on Phase 4.

**Phase 6 — Tests + hardening.** Objective: lock behavior.
Deliverables: unit tests for policy/decisions/merge/orphan recovery; integration test for a deny-with-message round trip; REPL smoke test.
Effort: 1–1.5 person-days. Team: 1 dev. Runs alongside Phases 2–5.

**Total: ~6–8 person-days, solo.**

## Critical Path
Phase 1 → Phase 2 → Phase 3 → Phase 4. Phase 5 is optional and off the critical path. Phase 6 runs continuously. The permission engine (1–2) is the hard, testable core; the
 UI (3–4) is justified by needing to talk to a human, not a separate ambition.

## Testing Strategy
- **Unit:** grading resolution, each decision type, rule-store hit/miss, ordered merge of approved+denied results, orphan recovery.
- **Integration:** a scripted resolver that denies with a message → assert the persisted tool message carries the right `tool_call_id` and content, and that the loop contin
ues.
- **Contract:** assert every assistant `tool_calls` entry has a matching `tool` result after a turn, including denials.
- **REPL:** smoke test with piped input; assert `KeyboardInterrupt` returns to prompt and `shutdown()` fires once on exit.
- **Regression:** existing suite must pass with `AutoAllowResolver` injected by default in `conftest.py`.

## Risks & Challenges
- **Backward-compat vs. safety default.** One-shot `run` defaulting to `ask` breaks existing scripts; defaulting to `allow` is a security hole. Recommendation: `--permissio
n-mode` with default `ask`, fail-closed when not a TTY, and an explicit `--yes` for scripts. Flag this as a decision point.
- **`run_command` is coarse.** `ls` and `rm -rf /` grade identically. Accepted for v1; the rule interface must be able to see `fn_args` so argument-level rules slot in late
r without a rewrite.
- **Streaming coupling.** Until Phase 5, REPL output is whatever `print()` emits. Acceptable, but the sink refactor is the clean fix.
- **In-memory rules reset on restart.** Expected given persistence is on hold; the `RuleStore` protocol ensures the swap is mechanical.

## MVP Scope
Phases 1–4. Static per-tool grading, in-memory rules, interactive prompt with all five decisions, deny-with-message round trip, REPL with core meta-commands, orphan recover
y, fail-closed non-interactive default. No persistence, no argument-level rules, no sink refactor.

## Future Enhancements
- **Persistence (deferred):** SQLite `tool_permissions` table keyed by `(scope, scope_key, tool_name)`; scopes `session` / `project` (`FILES_BASE_DIR`) / `global`; swap `In
MemoryRuleStore` for a SQLite implementation behind the existing protocol.
- **Argument-level rules** for `run_command` (pattern matching on the command string).
- **`/permissions` management UI** to view and revoke rules.
- **Streaming sink** for richer rendering (Phase 5).
- **`rich`/`prompt_toolkit`** upgrade for prompt rendering and line editing.