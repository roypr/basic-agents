# basic-agents

A simple, extendable agentic framework for running LLM-powered agents against any OpenAI-compatible local or remote inference server.

## Overview

`basic-agents` provides a lightweight CLI harness for building and running specialized agents. Each agent lives in its own directory under `agents/` and follows a naming convention that lets the framework load it dynamically. Sessions are persisted to a local SQLite database so conversations can be resumed across runs.

The framework is intentionally minimal — no heavy dependencies, no cloud lock-in. It works out of the box with local inference servers like `llama-server` (llama.cpp / ik_llama.cpp) and should work with any OpenAI-compatible endpoint.

## Project Structure

```
basic-agents/
├── agents/              # One subdirectory per agent (e.g. agents/default/)
│   └── <name>/
│       └── agent.py    # Must export a class named <Name>Agent
├── core/                # Base agent class and shared loop logic
├── db/                  # SQLite session persistence (session_db.py)
├── utils/               # File and query helpers (file_utils.py)
├── tests/               # pytest test suite
├── main.py              # CLI entry point
├── session_utils.py     # Session management CLI
├── config.py            # Global config (FILES_BASE_DIR)
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

## Running an Agent

```bash
python main.py \
  --agent default \
  --query "What files are in my workspace?" \
  --llm-base http://localhost:8080 \
  --model local \
  --max-turns 10
```

### CLI Options

| Flag | Default | Description |
|---|---|---|
| `--agent` | `default` | Which agent to run (must match a directory under `agents/`) |
| `--query` | `""` | The question or task to send to the agent |
| `--llm-base` | `http://localhost:8080` | Base URL of the LLM inference server |
| `--model` | `local` | Model name passed to the server |
| `--max-turns` | `10` | Maximum tool-call rounds before stopping |
| `--api-key` | `""` | Bearer token for external API endpoints |
| `--files-base-dir` | `/workspace` | Base directory exposed to file tools |
| `--include` | — | Path to a file whose contents are appended to the query |
| `--lines` | — | Line range from `--include`, e.g. `10-20` or `20` |
| `--resume-session` | — | Resume an existing session by its integer ID |
| `--session-name` | `Default Session` | Name for a new session |

### Including File Context

You can inline a file (or a slice of it) into your query:

```bash
# Include entire file
python main.py --agent default --query "Explain this code" --include ./myfile.py

# Include only lines 10–40
python main.py --agent default --query "What does this function do?" \
  --include ./myfile.py --lines 10-40
```

## Session Management

Sessions store conversation history in a local SQLite database, allowing agents to resume where they left off.

```bash
# Create a session with a custom system prompt
python session_utils.py create --name "My Project" --system-prompt "You are a coding assistant."

# List all active sessions
python session_utils.py list

# Export a session to JSON
python session_utils.py get --id 1

# Delete a session
python session_utils.py delete --id 1
```

To resume a session when running an agent:

```bash
python main.py --agent default --query "Continue" --resume-session 1
```

## Creating a Custom Agent

1. Create a directory under `agents/`:

```
agents/myagent/
├── __init__.py
└── agent.py
```

2. In `agent.py`, define a class named `MyagentAgent` (capitalized agent name + `Agent`):

```python
from core.base_agent import BaseAgent  # adjust import to match actual base class

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
python main.py --agent myagent --query "Do something" --llm-base http://localhost:8080
```

The framework discovers agents by directory name, so no registration step is needed.

## Configuration

`FILES_BASE_DIR` controls the root directory that file tools can access. It defaults to `/workspace` and can be overridden via environment variable or `--files-base-dir`:

```bash
export FILES_BASE_DIR=/home/user/projects
# or
python main.py --agent default --query "..." --files-base-dir /home/user/projects
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

Designed to work with any OpenAI-compatible `/v1/chat/completions` endpoint. Tested with local servers running via llama.cpp / ik_llama.cpp. Should work with remote providers (OpenAI, Together, etc.) by setting `--llm-base` and `--api-key` accordingly.