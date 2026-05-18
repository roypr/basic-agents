import argparse
import os
from importlib import import_module
from pathlib import Path

from utils.file_utils import build_query, parse_line_range


def get_agent_class(agent_name: str):
    agents_dir = Path(__file__).resolve().parent / "agents"
    if not (agents_dir / agent_name).is_dir():
        raise ValueError(f"Agent '{agent_name}' not found.")

    try:
        module = import_module(f"agents.{agent_name}.agent")
    except ModuleNotFoundError as exc:
        if exc.name == f"agents.{agent_name}.agent":
            raise ValueError(f"Agent '{agent_name}' not found.") from exc
        raise

    class_name = f"{agent_name.capitalize()}Agent"
    agent_cls = getattr(module, class_name, None)
    if agent_cls is None:
        raise ValueError(f"Agent class '{class_name}' not found in agents.{agent_name}.agent.")
    return agent_cls


def main():
    parser = argparse.ArgumentParser(description="Run a specialized basic agent")
    parser.add_argument("--agent", default="default",
                        help="Select which agent to run")
    parser.add_argument("--files-base-dir", default=None,
                        help="Optional base directory for file tools (default /workspace)")
    parser.add_argument("--query", default="", help="The question or task for the agent")
    parser.add_argument("--include", default=None,
                        help="Optional file path to include content from")
    parser.add_argument("--lines", default=None,
                        help="Optional line range to include from --include, e.g. 10-20 or 20")
    parser.add_argument("--llm-base", default="http://localhost:8080", help="Base URL of the LLM server")
    parser.add_argument("--model", default="local", help="Model name")
    parser.add_argument("--max-turns", type=int, default=10, help="Max tool-call rounds")
    parser.add_argument("--api-key", default="", help="Bearer token for external API")
    parser.add_argument("--resume-session", type=int, help="Resume an existing session by ID")
    parser.add_argument("--session-name", default="Default Session", help="Name for the session")
    args = parser.parse_args()

    if args.files_base_dir:
        os.environ["FILES_BASE_DIR"] = args.files_base_dir

    if args.lines and not args.include:
        parser.error("--lines can only be used together with --include")

    try:
        line_range = parse_line_range(args.lines) if args.lines else None
    except ValueError as exc:
        parser.error(str(exc))

    combined_query = build_query(args.query, args.include, line_range)

    try:
        agent_cls = get_agent_class(args.agent)
    except ValueError as exc:
        parser.error(str(exc))

    agent = agent_cls(
        model=args.model,
        llm_base=args.llm_base,
        max_turns=args.max_turns,
        resume_session=args.resume_session,
        session_name=args.session_name,
        api_key=args.api_key,
    )
    agent.run(combined_query)


if __name__ == "__main__":
    main()
