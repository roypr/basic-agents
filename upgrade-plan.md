## Constraints that shape the choice

1. **Line-buffered REPL** — the loop uses `input()`, so there's no raw keypress mode. You can't intercept "press Tab to expand" mid-stream without switching to a raw termin
al library, which is a big architectural change and breaks piped/test output.
2. **Zero-dependency styling** — `cli/style.py` is pure ANSI, deliberately no `rich`/`textual`. Introducing a TUI framework would be a heavy departure.
3. **Sink protocol is already the right seam** — `start/reasoning/content/end` cleanly separates the two streams. The rendering policy is entirely a sink decision; `llm_cli
ent` and `base_agent` don't need to change.
4. **Two distinct consumers** — `PrintSink` (one-shot `run`, the "I want to see everything" mode) vs `ReplSink` (interactive, where you want a cleaner UI). These should div
erge: keep `PrintSink` verbose, make `ReplSink` compact.

## What won't work well

- **ANSI scroll-region / alternate-screen tricks** — fragile across terminals (especially Windows `cmd`/PowerShell), breaks with piped output, and fights the streaming mode
l. Not worth it.
- **In-place expand/collapse** — requires cursor manipulation and a raw mode event loop. That's a full TUI rewrite, disproportionate to the goal.
- **`rich` Live/Panel** — works, but adds a hard dependency and would force rewriting the streaming path to fit `rich`'s refresh model. Overkill.

## Recommended approach: deferred rendering via the sink

The natural fit for a line-based REPL is **buffer reasoning, print a compact summary, expand on demand via a command**. This maps cleanly onto what you already have:

### How it behaves

```
[default:session 42] > How many grand slams has zverev won?

💭 thinking… (1,234 chars)  ← dimmed, one line, printed when content starts

**Alexander Zverev has won 2 Grand Slam singles titles**…
                              ← content streams at full brightness
```

Then later:
```
[default:session 42] > /think
```
prints the full buffered reasoning for the last turn (dimmed).

### Why this fits the architecture

| Piece | Change |
|---|---|
| `ReplSink.reasoning()` | Buffer into `self._reasoning_buffer` instead of printing. Print a dimmed one-liner (`💭 thinking… (N chars) — /think to expand`) **once**, at the
 moment content first arrives (in `content()`) or at `end()` if there was no content. |
| `ReplSink` | Expose `self.last_reasoning` (the buffer, preserved after `end()`). |
| `ReplSink.start()` | Reset the buffer; keep the turn separator. |
| `Repl` | Add a `/think` command that prints `self._sink.last_reasoning` (dimmed). |
| `PrintSink` | **Unchanged** — stays verbose for the one-shot `run` path. You add your `dim([/Think])` here. |
| `StreamSink` protocol | **Unchanged** — same four methods. |
| `llm_client` / `base_agent` | **Unchanged** — they already route deltas correctly. |

### Key design points

1. **Reasoning is not lost** — it's buffered and retrievable. You just don't see it flood the terminal by default.
2. **Progress feedback preserved** — the dimmed `💭 thinking…` line tells the user the model is working, so there's no silent gap during long reasoning.
3. **Content gets full prominence** — streams at normal brightness with no reasoning mixed in, solving the "can't tell where content starts" problem entirely.
4. **Char count on the summary** — gives a sense of how much thinking happened without showing it, which is usually enough to decide whether to `/think`.
5. **No new dependencies, no terminal fragility** — pure Python + the ANSI helpers you already have.
6. **Piped/test output stays sane** — `BufferSink` already proves the pattern; a piped REPL would just print the summary line as plain text.

### Optional refinements (later, not now)

- **`/think on` / `/think off`** — a toggle for users who want inline reasoning streaming back (equivalent to falling back to `PrintSink` behavior for reasoning).
- **Truncate `/think` output** — if reasoning is very long, print first/last N lines with a `… (M more lines)` marker in between.
- **Multi-turn** — `last_reasoning` only holds the most recent turn. A `/think N` or `/think last` could index into a small ring buffer if you want history.

### The one thing to watch

The summary line must be printed at the **right moment**: when `content()` is first called (reasoning just finished, content is starting) — not at `end()`, because tool-cal
l-only turns (no content) would get a misleading summary. If a turn has reasoning but no content (only tool calls), print the summary at `end()` instead. The sink already t
racks `_content_started`, so this branch is straightforward.

This is the minimal, architecture-consistent way to get "expandable reasoning" in a line-based terminal REPL without pulling in a TUI framework or fighting terminal capabil
ities.
[Finish] Analyzed the reasoning/content separation issue and recommended a deferred-rendering approach for ReplSink (buffer reasoning, print a compact dimmed summary, expan
d via /think command) that fits the existing sink architecture with no new dependencies. No code was written per the user's request.
