import argparse
import os
import sys
from importlib import import_module
from pathlib import Path

from cli.resolver import build_resolver
from utils.file_utils import build_query, encode_image_base64, parse_line_range
from utils.provider_config import ProviderError, resolve_provider


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
        raise ValueError(
            f"Agent class '{class_name}' not found in agents.{agent_name}.agent."
        )
    return agent_cls


def resolve_llm_config(provider, model, llm_base, api_key):
    """Resolve provider/model config, returning (llm_base, api_key, model)."""
    if llm_base is not None:
        return llm_base, api_key or "", model or "local"
    provider_cfg = resolve_provider(provider, model)
    return provider_cfg.api_base_url, provider_cfg.api_key, provider_cfg.model


def resolve_resume_session(args):
    """Resolve --continue / --resume-session into a session ID (or None)."""
    session_id = args.resume_session
    if args.continue_flag:
        if args.resume_session is not None:
            print("Error: --continue and --resume-session are mutually exclusive.")
            sys.exit(1)
        from db.session_db import SessionDB

        db = SessionDB()
        session_id = db.get_latest_active_session_id()
        if session_id is None:
            print("Error: No active sessions to continue.")
            sys.exit(1)
        print(f"[Continue] Resuming latest session (ID: {session_id})")
    return session_id


def _effective_permission_mode(args, interactive: bool) -> str:
    """Resolve the permission mode from the flags.

    ``--yes`` forces ``allow`` (the non-interactive escape hatch). Otherwise an
    explicit ``--permission-mode`` wins; the fallback is ``allow`` for one-shot
    ``run`` (backward compatible) and ``ask`` for the interactive REPL.
    """
    if getattr(args, "yes", False):
        return "allow"
    mode = getattr(args, "permission_mode", None)
    if mode:
        return mode
    return "ask" if interactive else "allow"


def do_run(args):
    """Handle the 'run' subcommand (and backward-compatible bare calls)."""
    if args.list_providers:
        from utils.provider_config import list_models

        for provider, models in list_models().items():
            print(f"[{provider}]")
            for m in models:
                print(f"  - {m}")
        return

    # --interactive routes to the REPL (shared code path with the `chat` cmd).
    if getattr(args, "interactive", False):
        return do_chat(args)

    session_id = resolve_resume_session(args)

    if args.files_base_dir:
        os.environ["FILES_BASE_DIR"] = args.files_base_dir

    if args.lines and not args.include:
        print("Error: --lines can only be used together with --include")
        sys.exit(1)

    try:
        line_range = parse_line_range(args.lines) if args.lines else None
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    combined_query = build_query(args.query, args.include, line_range)

    # Process --image if provided
    image_data = None
    if args.image:
        try:
            mime, b64 = encode_image_base64(args.image)
            image_data = {"mime": mime, "data": b64}
            print(f"[Image] Loaded {args.image} ({mime}, {len(b64)} base64 chars)")
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}")
            sys.exit(1)

    try:
        agent_cls = get_agent_class(args.agent)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    try:
        llm_base, api_key, model = resolve_llm_config(
            args.provider, args.model, args.llm_base, args.api_key
        )
    except ProviderError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if args.llm_base is None:
        print(f"[Provider] {args.provider or 'default'} -> {model} @ {llm_base}")

    resolver = build_resolver(
        _effective_permission_mode(args, interactive=False), scope="session"
    )
    agent = agent_cls(
        model=model,
        llm_base=llm_base,
        max_turns=args.max_turns,
        resume_session=session_id,
        session_name=args.session_name,
        api_key=api_key,
        permission_resolver=resolver,
    )

    try:
        agent.run(combined_query, image_data=image_data)
    except KeyboardInterrupt:
        print("\nInterrupted by user. Shutting down gracefully...")
        sys.exit(0)
    finally:
        agent.shutdown()


def do_chat(args):
    """Handle the 'chat' subcommand (interactive REPL)."""
    from cli.repl import run_repl

    if args.files_base_dir:
        os.environ["FILES_BASE_DIR"] = args.files_base_dir

    args.resume_session = resolve_resume_session(args)
    return run_repl(
        args, permission_mode=_effective_permission_mode(args, interactive=True)
    )


def do_session(args):
    """Handle the 'session' subcommand."""
    from utils import session_utils

    cmd = args.session_command
    if cmd == "create":
        session_utils.create_session(args.name, args.system_prompt)
    elif cmd == "list":
        session_utils.list_sessions()
    elif cmd == "get":
        session_utils.get_session(args.id)
    elif cmd == "delete":
        session_utils.delete_session(args.id)
    elif cmd == "compress":
        exit_code = session_utils.compress_session_cmd(
            args.id,
            output_dir=args.output_dir,
            summarize=args.summarize,
            provider=args.provider,
            model=args.model,
            llm_base=args.llm_base,
            api_key=args.api_key or "",
        )
        sys.exit(exit_code)
    else:
        print("Unknown session command. Available: create, list, get, delete, compress")
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        description="Basic Agents — run agents and manage sessions"
    )

    subparsers = parser.add_subparsers(dest="command")

    def add_common_agent_args(p):
        p.add_argument("--agent", default="default", help="Select which agent to run")
        p.add_argument(
            "--files-base-dir",
            default=None,
            help="Optional base directory for file tools (default /workspace)",
        )
        p.add_argument(
            "--provider",
            default=None,
            help="Provider name from providers.json (default: providers.json default_provider)",
        )
        p.add_argument(
            "--model",
            default=None,
            help="Model name for the chosen provider (default: providers.json default_model)",
        )
        p.add_argument(
            "--llm-base",
            default=None,
            help="Override LLM base URL (bypasses providers.json)",
        )
        p.add_argument(
            "--api-key", default=None, help="Override API key (bypasses providers.json)"
        )
        p.add_argument("--max-turns", type=int, default=10, help="Max tool-call rounds")
        p.add_argument(
            "--resume-session", type=int, help="Resume an existing session by ID"
        )
        p.add_argument(
            "--continue",
            dest="continue_flag",
            action="store_true",
            help="Resume the latest active session",
        )
        p.add_argument(
            "--session-name", default="Default Session", help="Name for the session"
        )
        p.add_argument(
            "--permission-mode",
            choices=("allow", "deny", "ask"),
            default=None,
            help="Tool permission mode (default: allow for run, ask for chat)",
        )
        p.add_argument(
            "--yes",
            action="store_true",
            help="Auto-approve all tool calls (alias for --permission-mode allow)",
        )

    # --- run subcommand (default) ---
    run_parser = subparsers.add_parser("run", help="Run an agent")
    add_common_agent_args(run_parser)
    run_parser.add_argument(
        "--query", default="", help="The question or task for the agent"
    )
    run_parser.add_argument(
        "--include", default=None, help="Optional file path to include content from"
    )
    run_parser.add_argument(
        "--image", default=None, help="Optional image file path to send to the AI"
    )
    run_parser.add_argument(
        "--lines",
        default=None,
        help="Optional line range to include from --include, e.g. 10-20 or 20",
    )
    run_parser.add_argument(
        "--interactive",
        action="store_true",
        help="Launch the interactive REPL instead of a one-shot run",
    )
    run_parser.add_argument(
        "--list-providers",
        action="store_true",
        help="List configured providers and models, then exit",
    )

    # --- chat subcommand (interactive REPL) ---
    chat_parser = subparsers.add_parser("chat", help="Start an interactive REPL")
    add_common_agent_args(chat_parser)

    # --- session subcommand ---
    session_parser = subparsers.add_parser("session", help="Manage sessions")
    session_subparsers = session_parser.add_subparsers(dest="session_command")

    create_parser = session_subparsers.add_parser("create", help="Create a new session")
    create_parser.add_argument("--name", required=True, help="Name of the session")
    create_parser.add_argument(
        "--system-prompt", default="", help="System prompt content"
    )

    session_subparsers.add_parser("list", help="List active sessions")

    get_parser = session_subparsers.add_parser("get", help="Export a session to JSON")
    get_parser.add_argument("--id", type=int, required=True, help="Session ID")

    delete_parser = session_subparsers.add_parser(
        "delete", help="Mark a session as deleted"
    )
    delete_parser.add_argument("--id", type=int, required=True, help="Session ID")

    compress_parser = session_subparsers.add_parser(
        "compress", help="Compress a session"
    )
    compress_parser.add_argument(
        "--id", type=int, required=True, help="Session ID to compress"
    )
    compress_parser.add_argument(
        "--output-dir", default="logs", help="Directory for raw export (default: logs/)"
    )
    compress_parser.add_argument(
        "--summarize", action="store_true", help="Summarize after compressing"
    )
    compress_parser.add_argument(
        "--provider",
        default=None,
        help="Provider for summarization (default from providers.json)",
    )
    compress_parser.add_argument(
        "--model",
        default=None,
        help="Model for summarization (default from providers.json)",
    )
    compress_parser.add_argument(
        "--llm-base", default=None, help="Override LLM base URL for summarization"
    )
    compress_parser.add_argument(
        "--api-key", default=None, help="Override API key for summarization"
    )

    return parser


def main():
    parser = build_parser()

    # Backward compatibility: if no subcommand is given and --agent is used,
    # default to 'run' subcommand
    if len(sys.argv) > 1 and sys.argv[1] in ("run", "session", "chat"):
        args = parser.parse_args()
    else:
        # Default to 'run' subcommand
        sys.argv.insert(1, "run")
        args = parser.parse_args()

    if args.command == "run":
        do_run(args)
    elif args.command == "session":
        do_session(args)
    elif args.command == "chat":
        exit_code = do_chat(args)
        if exit_code:
            sys.exit(exit_code)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
