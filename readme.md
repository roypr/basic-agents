# basic-agents

> **Make agents do anything — with just a prompt and tools.**

A simple, extendable agentic framework for running LLM-powered agents against OpenAI-compatible, DeepSeek, OpenRouter, and Anthropic Messages endpoints.

## Overview

`basic-agents` provides a lightweight CLI harness for building and running specialized agents. Each agent lives in its own directory under `agents/` and follows a naming convention that lets the framework load it dynamically. Sessions are persisted to a local SQLite database so conversations can be resumed across runs.

One of the core motivations behind this project is **eliminating the massive context overhead that generalized agents carry by default**. Instead of one bloated agent that knows about everything, you build lean, purpose-specific agents — each carrying only the system prompt, tools, and context it actually needs. This keeps inference fast and costs low, especially for long-running tasks.

The framework is intentionally minimal — no heavy dependencies, no cloud lock-in. It works out of the box with local inference servers like `llama-server` (llama.cpp / ik_llama.cpp) and with any OpenAI-compatible remote endpoint including OpenRouter.

## Project Structure

```
basic-agents/
├── agents/              # One subdirectory per agent (e.g. agents/default/)
│   └── <name>/
│       ├── agent.py          # Must export a class named <Name>Agent
│       ├── system_prompt.txt # Agent-specific system prompt
│       └── tools.py          # Agent-specific tools
├── core/                # Base agent class, tool registry, and shared loop logic
├── utils/               # File helpers, LLM client, session manager, tool executor
│   └── mcp/             # Optional MCP tool servers (config, connection, adapter)
├── tests/               # pytest test suite
├── main.py              # CLI entry point with run + chat + session subcommands
├── config.py            # Global config (FILES_BASE_DIR)
├── providers.json       # Provider/model config (not committed — copy providers.example.json)
├── providers.example.json # Template for providers.json
├── mcp.json             # MCP servers (not committed — copy mcp.example.json)
├── mcp.example.json     # Template for mcp.json
├── requirements.txt     # Runtime deps: requests, ddgs, mcp (optional)
├── requirements-dev.txt # Dev deps: pytest, pytest-cov, pytest-mock, pytest-asyncio, python-dotenv
└── pytest.ini           # pytest config
```

## Installation

```bash
git clone https://github.com/roypr/basic-agents.git
cd basic-agents

pip install -r requirements.txt

# For development / running tests
pip install -r requirements-dev.txt
```

## Usage

The CLI uses subcommands. The main commands are `run` (to execute an agent once), `chat` (for an interactive REPL), and `session` (to manage sessions).

### Running an Agent

Providers and models are configured in `providers.json` (see [Providers](#providers)). The default provider and model are picked up automatically, so the simplest invocation is:

```bash
python main.py run \
  --agent default \
  --query "What files are in my workspace?"
```

> **Note:** For backward compatibility, you can omit the `run` subcommand — the old flat-style invocation still works:
> ```bash
> python main.py --agent default --query "What files are in my workspace?"
> ```

To pick a specific provider and model:

```bash
python main.py run \
  --agent default \
  --provider deepseek \
  --model deepseek-v4-flash \
  --query "Refactor this module" \
  --max-turns 50
```

List everything configured in `providers.json`:

```bash
python main.py run --list-providers
```

### Overriding the Endpoint Directly

If you want to bypass `providers.json` entirely (e.g. for a one-off local server), pass `--llm-base`, `--model`, and optionally `--api-key`:

```bash
python main.py run \
  --agent default \
  --query "Quick question" \
  --llm-base http://localhost:8080 \
  --model local \
  --api-key sk-...
```

This works with any provider that exposes an OpenAI-compatible `/chat/completions` endpoint.

### Long-running Tasks

For tasks that span many tool calls — large refactors, file processing pipelines, multi-step research — set `--max-turns` generously. If the agent hits the limit before finishing, resume from where it left off.

Resume a specific session by ID:

```bash
# Start a long task
python main.py run --agent default --query "Audit and fix all TODO comments in the repo" \
  --max-turns 100 --session-name "todo-audit"

# If it stops, resume by session ID
python main.py run --agent default --query "Continue" --resume-session 3 --max-turns 100
```

Or resume the latest active session automatically with `--continue`:

```bash
python main.py run --agent default --query "Continue" --continue --max-turns 100
```

> **Note:** `--continue` and `--resume-session` are mutually exclusive.

Sessions persist the full conversation history, so the agent picks up with complete context intact.

### CLI Options — `run` subcommand

| Flag | Default | Description |
|---|---|---|
| `--agent` | `default` | Which agent to run (must match a directory under `agents/`) |
| `--query` | `""` | The question or task to send to the agent |
| `--provider` | `providers.json` default | Provider name from `providers.json` |
| `--model` | `providers.json` default | Model name for the chosen provider |
| `--llm-base` | — | Override the LLM base URL (bypasses `providers.json`) |
| `--api-key` | — | Override the API key (bypasses `providers.json`) |
| `--max-turns` | `10` | Maximum tool-call rounds before stopping — set generously for long tasks |
| `--max-output-tokens` | provider default | Output token budget. Mapped per provider: OpenAI `max_completion_tokens`, Anthropic/DeepSeek/OpenRouter `max_tokens` |
| `--reasoning-effort` | provider default | Reasoning effort: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `--files-base-dir` | `/workspace` | Base directory exposed to file tools |
| `--include` | — | Path to a file whose contents are appended to the query |
| `--lines` | — | Line range from `--include`, e.g. `10-20` or `20` |
| `--image` | — | Path to an image to send with the query |
| `--resume-session` | — | Resume an existing session by ID |
| `--continue` | — | Resume the latest active session (mutually exclusive with `--resume-session`) |
| `--session-name` | `Default Session` | Name for a new session |
| `--list-providers` | — | List configured providers and models, then exit |
| `--interactive` | — | Launch the interactive REPL instead of a one-shot run |

### Attaching File and Image Context

You can inline a file (or a slice of it) into your query:

```bash
# Include entire file
python main.py run --agent default --query "Explain this code" --include ./myfile.py

# Include only lines 10–40
python main.py run --agent default --query "What does this function do?" \
  --include ./myfile.py --lines 10-40
```

To send an image alongside your query, use `--image`:

```bash
python main.py run --agent default --query "What does this screenshot show?" \
  --image ./screenshot.png
```

`--lines` requires `--include`. Both `--include` and `--image` also work in the interactive REPL — see [Interactive Mode](#interactive-mode-chat).

## Interactive Mode (`chat`)

The `chat` subcommand starts a persistent REPL that keeps a single agent and session alive across turns:

```bash
python main.py chat --agent default --provider deepseek --model deepseek-flash
```

`run --interactive` launches the same REPL.

### REPL Commands

| Command | Description |
|---|---|
| `/help` | Show the command list and startup options |
| `/quit`, `/exit` | Shut down and exit |
| `/new` | Start a fresh session on the next turn |
| `/session` | Show the bound session ID and name |
| `/agents [name]` | List agents, or switch to `<name>` |
| `/provider [name]` | Show or switch the provider |
| `/model [name]` | Show or switch the model |
| `/image <path>` | Attach an image to the next turn |
| `/file <path> [lines]` | Attach a file's contents to the next turn |

Anything that is not a `/` command is sent to the agent as a prompt. Switching the provider or model mid-session re-instantiates the agent but keeps the current session bound, so the conversation continues without losing context.

### Attaching Context at Startup

The REPL accepts the same attachment flags as `run`. They apply to the **first turn only** — later turns rely on the session history, which already contains the attached content:

```bash
python main.py chat --include ./myfile.py --lines 10-40
python main.py chat --image ./screenshot.png
```

### Attaching Context Mid-Session

Use the `/image` and `/file` commands to attach context to the **next turn** without restarting. Like the startup flags, both are one-shot — the attachment rides the next prompt and is then cleared:

```
[default:new session] > /file ./myfile.py 10-40
[REPL] File queued for the next turn: ./myfile.py (lines 10-40)
[default:new session] > /image ./screenshot.png
[REPL] Image queued for the next turn: ./screenshot.png (image/png)
[default:new session] > summarise this file and describe the screenshot
```

`/file` accepts an optional trailing line range (`10-40` or `20`); omit it to attach the whole file. Multiple `/file` commands queue together. `/image` sends a single image, so a later `/image` replaces any image still queued.


Because the REPL is provider-driven, `--llm-base` and `--api-key` are ignored in chat mode. Base URL and API key always come from the provider selected in `providers.json` (via `--provider`, `/provider`, or the configured default).

### Terminal Styling

The CLI output is lightly styled for readability — the banner, the input prompt, permission headers and the streamed `[Think]` marker each use a color or dim tone so turns and gates are easy to scan. This is purely cosmetic and adds no dependencies: styling lives in a tiny ANSI helper (`cli/style.py`) and colors are emitted only when the output is an interactive terminal.

Redirected or piped output, logs and captured test output are always plain text, so scripting and `pytest` are unaffected. To force plain output explicitly, set the standard `NO_COLOR` environment variable (or run with `TERM=dumb`):

```bash
NO_COLOR=1 python main.py chat
```

Reasoning text is buffered and printed as one block rather than streamed token-by-token. This keeps CJK reasoning readable and avoids legacy Windows console encoding failures. Console output is also configured to use UTF-8 where the terminal supports it, with escaped output as a fallback.

## Session Management

Sessions store conversation history in a local SQLite database, allowing agents to resume where they left off. All session operations are available via the `session` subcommand:

```bash
# Create a session with a custom system prompt
python main.py session create --name "My Project" --system-prompt "You are a coding assistant."

# List all active sessions
python main.py session list

# Export a session to JSON
python main.py session get --id 1

# Delete a session
python main.py session delete --id 1
```

### Compressing Sessions

Long sessions can be compressed to save storage and reduce context overhead. This exports the raw conversation to a JSON file and replaces it with a compressed summary:

```bash
python main.py session compress --id 1
```

Options for compression:

| Flag | Default | Description |
|---|---|---|
| `--id` | (required) | Session ID to compress |
| `--output-dir` | `logs` | Directory for the raw JSON export |
| `--summarize` | — | Also generate an LLM summary after compression |
| `--provider` | `providers.json` default | Provider for summarization |
| `--model` | `providers.json` default | Model for summarization |
| `--llm-base` | — | Override LLM base URL for summarization |
| `--api-key` | — | Override API key for summarization |

### Resuming a Session

When running an agent, resume a specific session by ID:

```bash
python main.py run --agent default --query "Continue" --resume-session 1
```

Or resume the latest active session automatically:

```bash
python main.py run --agent default --query "Continue" --continue
```

## Creating a Custom Agent

1. Create a directory under `agents/`:

```
agents/myagent/
├── __init__.py
├── agent.py
├── system_prompt.txt   # (optional) Agent-specific system prompt
└── tools.py            # (optional) Agent-specific tools
```

2. In `agent.py`, define a class named `MyagentAgent` (capitalized agent name + `Agent`):

```python
from core.base_agent import BaseAgent

class MyagentAgent(BaseAgent):
    def __init__(self, model, llm_base, max_turns, resume_session, session_name, api_key):
        super().__init__(
            model=model,
            llm_base=llm_base,
            max_turns=max_turns,
            resume_session=resume_session,
            session_name=session_name,
            api_key=api_key,
        )

    def run(self, query: str):
        # Implement your agent loop here
        ...
```

3. Run it:

```bash
python main.py run --agent myagent --query "Do something"
```

The framework discovers agents by directory name, so no registration step is needed.

## Providers

Model endpoints are configured in `providers.json` at the project root. Each provider declares a `type`, base URL, API key, and the models it exposes. The framework currently supports four wire protocols:

| `type` | Endpoint | Auth | Reasoning control | Output budget field |
|---|---|---|---|---|
| `openai` | `/v1/chat/completions` | `Authorization: Bearer` | `reasoning_effort` | `max_completion_tokens` |
| `deepseek` | `/chat/completions` | `Authorization: Bearer` | `reasoning_effort` + `thinking` | `max_tokens` |
| `openrouter` | `/api/v1/chat/completions` | `Authorization: Bearer` | `reasoning: {effort}` | `max_tokens` |
| `anthropic` | `/v1/messages` | `x-api-key` + `anthropic-version` | `thinking` + `output_config.effort` | `max_tokens` (required) |

`type` is optional: if omitted, the adapter is inferred from the provider name, base URL, or model name. Any other OpenAI-compatible endpoint can use `type: "openai_compatible"`.

```json
{
  "default_provider": "deepseek",
  "default_model": "deepseek-flash",
  "providers": [
    {
      "name": "deepseek",
      "type": "deepseek",
      "api_base_url": "https://api.deepseek.com",
      "api_key": "sk-...",
      "models": ["deepseek-flash", "deepseek-v4-pro"],
      "max_tokens": 8192,
      "reasoning_effort": "high"
    },
    {
      "name": "openai",
      "type": "openai",
      "api_base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-6-astra", "gpt-5.6-terra"],
      "max_tokens": 8192,
      "reasoning_effort": "medium"
    },
    {
      "name": "openrouter",
      "type": "openrouter",
      "api_base_url": "https://openrouter.ai/api/v1",
      "api_key": "sk-or-v1-...",
      "models": ["deepseek/deepseek-v4-pro", "z-ai/glm-5.3-flash"],
      "max_tokens": 8192
    },
    {
      "name": "anthropic",
      "type": "anthropic",
      "api_base_url": "https://api.anthropic.com",
      "api_key": "sk-ant-...",
      "models": ["claude-opus-5-5", "claude-sonnet-5-5"],
      "max_tokens": 8192,
      "reasoning_effort": "high"
    }
  ]
}
```

- **`default_provider` / `default_model`** — used when `--provider` / `--model` are omitted.
- **`providers[].type`** — selects the adapter; inferred when omitted.
- **`providers[].models`** — list of valid model names for `--model`.
- **`providers[].max_tokens`** — provider-neutral output budget (Anthropic defaults to `8192` because the field is required).
- **`providers[].reasoning_effort`** — default reasoning effort; `--reasoning-effort` overrides it.

Provider-specific reasoning history is preserved automatically: DeepSeek `reasoning_content` (when tools are present), OpenRouter `reasoning_details`, and Anthropic signed `thinking` blocks survive a session resume. Reasoning is not sent to providers that do not accept it.

A `providers.example.json` template is included. Copy it to get started:

```bash
cp providers.example.json providers.json
# then edit providers.json with your real API keys
```

> `providers.json` contains secrets and should **not** be committed. It is already covered by `.gitignore`.

## MCP Tool Servers

[Model Context Protocol](https://modelcontextprotocol.io) servers expose JSON-Schema tools, which map one-to-one onto this framework's tool list. MCP is therefore just another *tool source*: the agent loop, provider adapters, session database and permission gate are unchanged, and MCP tools work with every provider (OpenAI, DeepSeek, OpenRouter, Anthropic, local servers).

### Setup

```bash
pip install "mcp"                 # optional dependency (lazy-imported)
cp mcp.example.json mcp.json      # then edit commands/args
```

`mcp.json` is read at startup; it is gitignored because it may contain tokens.

```json
{
  "enabled": true,
  "safe_tools": [],
  "servers": {
    "filesystem": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
      "agents": ["code"],
      "allowed_tools": ["read_file", "list_directory"],
      "denied_tools": [],
      "timeout": 60,
      "required": false,
      "inherit_env": true,
      "expose_resources": false,
      "expose_prompts": false
    },
    "github": {
      "transport": "http",
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": { "Authorization": "Bearer ${GITHUB_TOKEN}" },
      "agents": [],
      "timeout": 120
    }
  }
}
```

| Field | Meaning |
|---|---|
| `transport` | `stdio` (default), `http` (Streamable HTTP; aliases `streamable-http`, `streamable_http`) or `sse` (legacy) |
| `command` / `args` | Process launched over stdio |
| `url` | Endpoint for `http` / `sse` transports (must be `http://` or `https://`) |
| `headers` | Extra HTTP headers for `http` / `sse`; supports `${VAR}` and `${VAR:-default}` |
| `env` | Extra environment variables for stdio; supports `${VAR}` and `${VAR:-default}` |
| `inherit_env` | Merge `os.environ` into the child (default `true`; needed for `npx`/`node`/`uv` on `PATH`) |
| `agents` | Restrict the server to those agent directories; empty/omitted means every agent |
| `allowed_tools` / `denied_tools` | Filter the exposed tools (keeps schemas small; `denied_tools` wins) |
| `timeout` | Per-call timeout in seconds (default 60) |
| `required` | Fail agent startup if the server cannot start (default `false`: warn and continue) |
| `expose_resources` / `expose_prompts` | Also expose the server's resources/prompts as tools (default `false`) |
| `safe_tools` (top level) | Public tool names to run without a permission prompt — exact, bare (`fs__read`) or prefix (`mcp__fs__*`) |

Transports are resolved against the installed SDK, including the renames across its 1.x line: `mcp.client.streamable_http.streamable_http_client` (2.x and newer 1.x) and `...streamablehttp_client` (older 1.x) are both found automatically. Auth headers are passed in whichever dialect the factory signature expects — a caller-owned `httpx2.AsyncClient` (2.x) or `httpx.AsyncClient` (1.x) — and fall back to a plain `headers` kwarg, so both SDK lines work without configuration.

### Resources and prompts

With `expose_resources` / `expose_prompts` enabled — and only when the server advertises the capability — each server adds two synthetic tools, rather than one tool per resource, so the schema payload stays small and templated URIs still work:

| Tool | Arguments |
|---|---|
| `mcp__<server>__list_resources` | — |
| `mcp__<server>__read_resource` | `uri` |
| `mcp__<server>__list_prompts` | — |
| `mcp__<server>__get_prompt` | `name`, `arguments` (object) |

If the server already exposes a real tool with the same name, the real tool wins and the synthetic one is skipped with a warning.

### Behaviour

- **Namespacing** — tools appear as `mcp__<server>__<tool>`, so two servers can expose the same generic name and permission rules read clearly. Names are sanitised to `[A-Za-z0-9_-]` and capped at 64 characters (a hash suffix keeps long names distinct).
- **Permissions** — MCP tools are **not** in `SAFE_TOOLS`, so they default to `ask` (fail-closed). An MCP server runs outside the `safe_path()` sandbox, so pre-approval is explicit via `safe_tools`.
- **Results** — text blocks are joined; an image block reuses the existing image marker, so the model sees the image natively for every provider; `isError` becomes an `Error: …` tool result; `structuredContent` is JSON-encoded when there is no text. Resource reads report binary payloads as `(binary resource <uri> [mime], N bytes)`; prompts render as a role-tagged transcript.
- **Failures** — a tool failure comes back as an `Error: …` tool result (the loop continues); a missing server is a startup warning, or an error with `"required": true`. Diagnostics go to `logs/app_errors.log`.
- **Lifecycle** — one connection per server is shared process-wide and reused across `/provider`, `/model` and `/agents` switches; connections close on `shutdown()` (`/quit`, EOF, or the end of a one-shot run).

### Flags and commands

| Flag / command | Effect |
|---|---|
| `--mcp-config <path>` | Use a specific config file (default: `./mcp.json` when present) |
| `--no-mcp` | Disable MCP for this run |
| `python main.py mcp list` | Connect to every configured server and report transport, tool count and capabilities |
| `/mcp` | REPL: show what MCP contributed to the current agent |
| `/mcp reconnect [name]` | REPL: drop a connection (or all) and re-discover tools after editing `mcp.json` |

`BASIC_AGENTS_NO_MCP=1` disables MCP process-wide (CI, sandboxes, debugging). An explicit `--mcp-config` path that does not exist is an error rather than a silent disable.

Startup output reports what was loaded:

```
[MCP] 6 tools from filesystem (6 tools), github (12 tools)
```

`mcp list` output looks like:

```
[filesystem] stdio -> npx (6 tools, agents: code) [resources]
[github] http -> https://api.githubcopilot.com/mcp/ (12 tools, agents: all) [resources, prompts]
```

The transport layer is verified against real SDK builds (stdio, Streamable HTTP and SSE, on both the `mcp` 1.x and 2.x lines) by the scripts in `tests/manual/mcp/`; re-run them after bumping the optional `mcp` requirement.
## Error Handling

Every provider reports failures differently (OpenAI-style `{"error": {...}}`, Anthropic's `{"type": "error", ...}`, plain-text gateway responses, HTML error pages, or an error event inside an otherwise successful stream). All of them are normalised in `utils/llm/errors.py` into one `LLMError` carrying the provider, model, HTTP status, a category (`auth`, `rate_limit`, `context_length`, `invalid_request`, `server`, `network`, …), a retryability flag and an actionable hint.

The terminal shows the short form; the raw wire body goes to `logs/app_errors.log`:

```
[LLM error] openai/gpt-6-luna — invalid request (HTTP 400)
  Function tools with reasoning_effort are not supported for gpt-6-luna ...
  hint: This model rejects `reasoning_effort` together with tools — retry with `--reasoning-effort none`.
  details: full response logged to logs/app_errors.log
```

Behaviour by entry point:

| Entry point | On a provider error |
|---|---|
| `chat` (REPL) | Prints the block, keeps the session and prompt alive |
| `run` (one-shot) | Prints the block, exits with status `1` (no traceback) |
| `session compress --summarize` | Prints the block, exits with status `1` |

Errors are **not** retried automatically: a streamed turn may already have printed partial output, so a retry is the user's decision. The log keeps the full body (truncated at 4 KB) for debugging, including the offending URL, provider, model and status.

## Configuration

`FILES_BASE_DIR` controls the root directory that file tools can access. It defaults to `/workspace` (or `./workspace` on Windows) and can be overridden via environment variable or `--files-base-dir`:

```bash
export FILES_BASE_DIR=/home/user/projects
# or
python main.py run --agent default --query "..." --files-base-dir /home/user/projects
```

## Running Tests

```bash
pytest
# or
python run_tests.py
```

## Dependencies

**Runtime**
- `requests` — HTTP client for LLM server communication
- `ddgs` — DuckDuckGo search (used by built-in search tools)
- `mcp` — optional; required only when `mcp.json` declares servers (see [MCP Tool Servers](#mcp-tool-servers))

**Dev**
- `pytest`, `pytest-cov`, `pytest-mock`, `pytest-asyncio` — testing
- `python-dotenv` — `.env` support for local dev

## Compatibility

Supports four provider protocols out of the box: **OpenAI**, **DeepSeek**, **OpenRouter**, and **Anthropic Messages**. Other OpenAI-compatible `/chat/completions` endpoints (including local llama.cpp / ik_llama.cpp servers) work with `type: "openai_compatible"` or by inference. For one-off runs against an unconfigured endpoint, use `--llm-base` / `--model` / `--api-key` to bypass `providers.json`.

Only parameters the selected provider understands are sent. In particular, OpenAI reasoning models receive `max_completion_tokens` rather than the deprecated `max_tokens`, and llama.cpp-only fields (`cache_prompt`, `reasoning_format`, `repeat_*`, `top_k`, `typical_p`, `timings_per_token`) are not sent to remote providers.
