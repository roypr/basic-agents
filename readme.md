# basic-agents

> **Make agents do anything — with just a prompt and tools.**

A simple, extendable agentic framework for running LLM-powered agents against any OpenAI-compatible local or remote inference server.

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
├── cli/                 # Interactive REPL, permission prompts, stream sink
├── db/                  # SQLite session persistence (session_db.py)
├── utils/               # File helpers, LLM client, session manager, tool executor
├── tests/               # pytest test suite
├── main.py              # CLI entry point with run + chat + session subcommands
├── config.py            # Global config (FILES_BASE_DIR)
├── providers.json       # Provider/model config (not committed — copy providers.example.json)
├── providers.example.json # Template for providers.json
├── requirements.txt     # Runtime deps: requests, ddgs
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

Anything that is not a `/` command is sent to the agent as a prompt. Switching the provider or model mid-session re-instantiates the agent but keeps the current session bound, so the conversation continues without losing context.

### Attaching Context at Startup

The REPL accepts the same attachment flags as `run`. They apply to the **first turn only** — later turns rely on the session history, which already contains the attached content:

```bash
python main.py chat --include ./myfile.py --lines 10-40
python main.py chat --image ./screenshot.png
```

Because the REPL is provider-driven, `--llm-base` and `--api-key` are ignored in chat mode. Base URL and API key always come from the provider selected in `providers.json` (via `--provider`, `/provider`, or the configured default).

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

Model endpoints are configured in `providers.json` at the project root. The file is intentionally lightweight — just providers, their API URLs/keys, and the models each exposes. No router, fallback, or transformer logic.

```json
{
  "default_provider": "deepseek",
  "default_model": "deepseek-v4-flash",
  "providers": [
    {
      "name": "deepseek",
      "api_base_url": "https://api.deepseek.com/chat/completions",
      "api_key": "sk-...",
      "models": ["deepseek-v4-pro", "deepseek-v4-flash"]
    },
    {
      "name": "local",
      "api_base_url": "http://localhost:8080/chat/completions",
      "api_key": "sk-",
      "models": ["claude-sonet-4.6"]
    }
  ]
}
```

- **`default_provider` / `default_model`** — used when `--provider` / `--model` are omitted.
- **`providers[].name`** — referenced by `--provider`.
- **`providers[].models`** — list of valid model names for `--model`.

A `providers.example.json` template is included. Copy it to get started:

```bash
cp providers.example.json providers.json
# then edit providers.json with your real API keys
```

> `providers.json` contains secrets and should **not** be committed. It is already covered by `.gitignore` if you add it there.

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

**Dev**
- `pytest`, `pytest-cov`, `pytest-mock`, `pytest-asyncio` — testing
- `python-dotenv` — `.env` support for local dev

## Compatibility

Designed to work with any OpenAI-compatible `/chat/completions` endpoint. Tested with local servers running via llama.cpp / ik_llama.cpp. Works with remote providers configured in `providers.json` — including **OpenRouter**, **DeepSeek**, OpenAI, Together AI, and others. For one-off runs against an unconfigured endpoint, use `--llm-base` / `--model` / `--api-key` to bypass `providers.json`.
