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
Anything else is sent to the agent as a prompt."""


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

    # ------------------------------------------------------------------ loop

    def run(self) -> int:
        try:
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

    def _run_turn(self, query: str) -> None:
        try:
            self.agent.run(query)
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
        from main import get_agent_class, resolve_llm_config

        agent_cls = get_agent_class(self.agent_name)
        llm_base, api_key, model = resolve_llm_config(
            self.provider, self.model, self.args.llm_base, self.args.api_key
        )
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
        parts = line.split()
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

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
        else:
            self._output(f"[REPL] Unknown command: {cmd}. Type /help.")
        return None

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
