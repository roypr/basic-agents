"""Interactive REPL hosting the permission prompts.

The REPL resolves provider/model/session once, holds a single agent instance
for the whole process, and dispatches per-turn input to ``agent.run``. The
agent is shut down exactly once — on ``/quit`` or EOF — never between turns
(``shutdown()`` permanently sets ``_shutdown_requested`` and tears down global
resources, so calling it per turn would kill the loop).
"""

from __future__ import annotations

import sys

from cli.resolver import build_resolver
from cli.sink import ReplSink

HELP_TEXT = """\
Commands:
  /help              Show this help
  /quit, /exit       Shut down and exit
  /new               Start a fresh session on the next turn
  /session           Show the bound session ID and name
  /agents [name]     List agents, or switch to <name>
  /provider [name]   Show or switch the provider
  /model [name]      Show or switch the model
  /image <path>      Attach an image to the next turn
  /file <path> [lines]  Attach a file's contents to the next turn
Anything else is sent to the agent as a prompt.

Startup options (pass when launching chat):
  --include <path>   Attach a file's contents to the first turn
  --lines <range>    Limit --include to a range, e.g. 10-20 or 20
  --image <path>     Attach an image to the first turn
  --provider <name>  Start with a specific provider
  --model <name>     Start with a specific model
  --agent <name>     Start with a specific agent
  --resume-session <id>, --continue   Resume an existing session

--include/--lines/--image apply to the first turn only. /image and /file
queue attachments for the next turn. Later turns rely on session history."""


class Repl:
    """A persistent interactive loop around the one-shot ``run`` path."""

    def __init__(
        self,
        args,
        permission_mode: str = "ask",
        input_fn=None,
        output_fn=None,
    ):
        self.args = args
        self._input = input_fn or input
        self._output = output_fn or print
        self.agent = None
        self.agent_name = args.agent
        self.provider = args.provider
        self.model = args.model
        # Set once the first turn binds a session; reused by later turns and by
        # agent/provider/model re-instantiation.
        self.resume_session = getattr(args, "resume_session", None)
        self._resolver = build_resolver(permission_mode, scope="session")
        # Frames streamed model output so REPL turns stay visually distinct.
        self._sink = ReplSink()
        self._shutdown_done = False
        # One-shot attachments (--include/--lines/--image) ride along with the
        # first turn; later turns rely on the persisted session history.
        self._include_block = None
        self._image_data = None
        self._attachments_pending = True
        # Attachments queued mid-session via /file and /image. They are one-shot
        # too: the next turn consumes them, then they are cleared.
        self._queued_includes: list[str] = []
        self._queued_image: dict | None = None

    # ------------------------------------------------------------------ loop

    def run(self) -> int:
        try:
            self._prepare_attachments()
            self._instantiate()
        except Exception as exc:  # startup failure — nothing to shut down
            self._output(f"[REPL] Could not start: {exc}")
            return 1

        self._print_banner()
        try:
            while True:
                try:
                    line = self._input(self._prompt_text())
                except EOFError:
                    self._output("\n[REPL] End of input — exiting.")
                    break
                except KeyboardInterrupt:
                    self._output("\n[REPL] Interrupted. Type /quit to exit.")
                    continue

                line = line.strip()
                if not line:
                    continue
                if line.startswith("/"):
                    if self._handle_command(line) == "quit":
                        break
                    continue
                self._run_turn(line)
        finally:
            self.shutdown()
        return 0

    def _prepare_attachments(self) -> None:
        """Resolve --include/--lines/--image into the first-turn payload.

        Mirrors the one-shot ``run`` path so the same flags behave identically
        when the REPL is started via ``chat`` (or ``run --interactive``).
        """
        from utils.file_utils import build_query, encode_image_base64, parse_line_range

        include = getattr(self.args, "include", None)
        lines = getattr(self.args, "lines", None)
        image = getattr(self.args, "image", None)

        if lines and not include:
            raise ValueError("--lines can only be used together with --include")

        line_range = parse_line_range(lines) if lines else None
        if include:
            # build_query with an empty query returns just the include block;
            # it is appended to the first prompt typed in the REPL.
            self._include_block = build_query("", include, line_range)

        if image:
            mime, b64 = encode_image_base64(image)
            self._image_data = {"mime": mime, "data": b64}
            self._output(f"[Image] Loaded {image} ({mime}, {len(b64)} base64 chars)")

    def _consume_attachments(self, query: str) -> tuple[str, dict | None]:
        """Attach any pending include/image payload to this turn.

        Startup attachments (--include/--lines/--image) ride the first turn;
        attachments queued with /file and /image ride the next turn. Both are
        one-shot: once consumed they are cleared so later turns stay text-only.
        """
        image_data = None
        if self._attachments_pending:
            self._attachments_pending = False
            if self._include_block:
                query = f"{query}\n\n{self._include_block}"
            image_data = self._image_data

        if self._queued_includes:
            query = f"{query}\n\n" + "\n\n".join(self._queued_includes)
            self._queued_includes = []
        if self._queued_image is not None:
            image_data = self._queued_image
            self._queued_image = None
        return query, image_data

    def _run_turn(self, query: str) -> None:
        query, image_data = self._consume_attachments(query)
        try:
            self.agent.run(query, image_data=image_data)
        except KeyboardInterrupt:
            # Per-turn interrupt: return to the prompt, session stays resumable.
            self._output("\n[REPL] Turn interrupted. Session is resumable.")
        except Exception as exc:
            self._output(f"[REPL] Turn failed: {exc}")
        finally:
            self.resume_session = self.agent.session_id

    def shutdown(self) -> None:
        """Shut the agent down exactly once (on /quit or EOF)."""
        if self._shutdown_done or self.agent is None:
            return
        self._shutdown_done = True
        try:
            self.agent.shutdown()
        except Exception as exc:  # never let teardown mask the exit
            self._output(f"[REPL] Shutdown error: {exc}")

    # ------------------------------------------------------ agent lifecycle

    def _instantiate(self, preserve_binding: bool = False) -> None:
        """Build the agent for the current agent/provider/model selection.

        ``preserve_binding`` is set when re-instantiating after a switch: the
        session is already bound, so we rebind directly instead of letting the
        first ``run()`` re-resolve it via ``init_session_db``.
        """
        from main import get_agent_class
        from utils.provider_config import resolve_provider

        agent_cls = get_agent_class(self.agent_name)
        # Chat mode is provider-driven: base URL, API key and model all come
        # from providers.json for the selected provider. The ``--llm-base`` /
        # ``--api-key`` overrides are a one-shot ``run`` escape hatch and are
        # deliberately ignored here so a ``/provider`` switch always picks up
        # that provider's own credentials.
        provider_cfg = resolve_provider(self.provider, self.model)
        llm_base = provider_cfg.api_base_url
        api_key = provider_cfg.api_key
        model = provider_cfg.model
        agent = agent_cls(
            model=model,
            llm_base=llm_base,
            max_turns=self.args.max_turns,
            resume_session=self.resume_session,
            session_name=self.args.session_name,
            api_key=api_key,
            permission_resolver=self._resolver,
            stream_sink=self._sink,
        )
        if preserve_binding and self.resume_session is not None:
            agent.session_id = self.resume_session
        self.agent = agent
        self.model = model

    def _reinstantiate(self) -> None:
        """Rebuild the agent after /agents, /provider or /model."""
        old = self.agent
        try:
            self._instantiate(preserve_binding=True)
        except Exception as exc:
            self.agent = old
            self._output(f"[REPL] Could not switch: {exc}")
            return
        if old is not None:
            # Keep in-memory permission rules and the resolver across a switch.
            self.agent.permission_policy.rule_store = old.permission_policy.rule_store
            self.agent.permission_policy.resolver = old.permission_policy.resolver

    # ---------------------------------------------------------- meta-commands

    def _handle_command(self, line: str):
        tokens = line.split(maxsplit=1)
        cmd = tokens[0].lower()
        # `rest` keeps the whole argument string (paths may contain spaces);
        # `arg` is the first token, which is what the switch commands expect.
        rest = tokens[1].strip() if len(tokens) > 1 else None
        arg = rest.split()[0] if rest else None

        if cmd in ("/quit", "/exit"):
            return "quit"
        if cmd == "/help":
            self._output(HELP_TEXT)
        elif cmd == "/new":
            self._start_new_session()
        elif cmd == "/session":
            self._show_session()
        elif cmd == "/agents":
            self._handle_agents(arg)
        elif cmd == "/provider":
            self._handle_provider(arg)
        elif cmd == "/model":
            self._handle_model(arg)
        elif cmd == "/image":
            self._handle_image(rest)
        elif cmd == "/file":
            self._handle_file(rest)
        else:
            self._output(f"[REPL] Unknown command: {cmd}. Type /help.")
        return None

    def _handle_image(self, rest: str | None) -> None:
        """Queue an image (--image equivalent) for the next turn."""
        if not rest:
            self._output("[REPL] Usage: /image <path>")
            return

        from utils.file_utils import encode_image_base64

        try:
            mime, b64 = encode_image_base64(rest)
        except (FileNotFoundError, ValueError, OSError) as exc:
            self._output(f"[REPL] Could not attach image: {exc}")
            return
        self._queued_image = {"mime": mime, "data": b64}
        self._output(f"[REPL] Image queued for the next turn: {rest} ({mime})")

    def _handle_file(self, rest: str | None) -> None:
        """Queue a file include (--include/--lines equivalent) for the next turn."""
        if not rest:
            self._output("[REPL] Usage: /file <path> [start-end]")
            return

        from utils.file_utils import build_query

        path, line_range = self._split_path_and_range(rest)
        try:
            block = build_query("", path, line_range)
        except (FileNotFoundError, ValueError, OSError) as exc:
            self._output(f"[REPL] Could not attach file: {exc}")
            return
        self._queued_includes.append(block)
        label = f"lines {line_range[0]}-{line_range[1]}" if line_range else "whole file"
        self._output(f"[REPL] File queued for the next turn: {path} ({label})")

    @staticmethod
    def _split_path_and_range(rest: str) -> tuple[str, tuple[int, int] | None]:
        """Split ``<path> [start-end]`` into a path and an optional line range.

        A trailing token is treated as a range only when it parses as one, so
        paths containing spaces still work when no range is given.
        """
        from utils.file_utils import parse_line_range

        tokens = rest.split()
        if len(tokens) > 1:
            try:
                line_range = parse_line_range(tokens[-1])
            except ValueError:
                line_range = None
            if line_range is not None:
                return " ".join(tokens[:-1]), line_range
        return rest, None

    def _start_new_session(self) -> None:
        self.agent.session_id = None
        self.agent.resume_session = None
        self.resume_session = None
        self._output("[REPL] New session will be created on the next turn.")

    def _show_session(self) -> None:
        session_id = self.agent.session_id
        if session_id is None:
            self._output(
                f"[REPL] No session bound yet (name: {self.args.session_name})."
            )
        else:
            self._output(
                f"[REPL] Session ID: {session_id} (name: {self.args.session_name})"
            )

    def _handle_agents(self, arg) -> None:
        if not arg:
            self._output("[REPL] Available agents: " + ", ".join(self._list_agents()))
            self._output(f"[REPL] Current agent: {self.agent_name}")
            return

        from main import get_agent_class

        try:
            get_agent_class(arg)
        except ValueError as exc:
            self._output(f"[REPL] {exc}")
            return
        self.agent_name = arg
        self._reinstantiate()
        self._output(f"[REPL] Now using agent '{self.agent_name}'.")

    def _handle_provider(self, arg) -> None:
        if not arg:
            self._output(
                f"[REPL] Provider: {self.provider or '(default)'}  model: {self.model}"
            )
            return
        self.provider = arg
        # Models are provider-specific, so drop the current one and let the new
        # provider's default (with its own API key/base) be resolved.
        self.model = None
        self._reinstantiate()
        self._output(f"[REPL] Now using provider '{self.provider}'.")

    def _handle_model(self, arg) -> None:
        if not arg:
            self._output(f"[REPL] Model: {self.model}")
            return
        self.model = arg
        self._reinstantiate()
        self._output(f"[REPL] Now using model '{self.model}'.")

    @staticmethod
    def _list_agents() -> list:
        from pathlib import Path

        agents_dir = Path(__file__).resolve().parent.parent / "agents"
        if not agents_dir.is_dir():
            return []
        return sorted(
            p.name
            for p in agents_dir.iterdir()
            if p.is_dir() and (p / "agent.py").exists()
        )

    # ------------------------------------------------------------- rendering

    def _print_banner(self) -> None:
        self._output("")
        self._output("basic-agents REPL — /help for commands, /quit to exit.")
        self._output(f"  agent: {self.agent_name}   model: {self.model}")
        self._output(f"  session name: {self.args.session_name}")

    def _prompt_text(self) -> str:
        session_id = self.agent.session_id if self.agent else None
        tag = f"session {session_id}" if session_id else "new session"
        return f"[{self.agent_name}:{tag}] > "


def run_repl(args, permission_mode: str = "ask", input_fn=None, output_fn=None) -> int:
    """Entry point used by ``main.do_chat``."""
    return Repl(
        args,
        permission_mode=permission_mode,
        input_fn=input_fn,
        output_fn=output_fn,
    ).run()


def main(argv=None):  # pragma: no cover - convenience for `python -m cli.repl`
    from main import build_parser, _effective_permission_mode, resolve_resume_session

    parser = build_parser()
    args = parser.parse_args(argv)
    args.resume_session = resolve_resume_session(args)
    return run_repl(args, permission_mode=_effective_permission_mode(args, True))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
