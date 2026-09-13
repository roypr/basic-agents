import argparse
from types import SimpleNamespace

import pytest

from cli.repl import Repl, run_repl


class _ScriptedInput:
    """Returns queued lines; raises EOFError when exhausted."""

    def __init__(self, lines):
        self._lines = list(lines)

    def __call__(self, prompt_text=""):
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


def _args(**overrides):
    values = dict(
        agent="default",
        files_base_dir=None,
        provider=None,
        model=None,
        llm_base=None,
        api_key=None,
        max_turns=10,
        resume_session=None,
        session_name="Test Session",
        permission_mode=None,
        yes=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def _fake_agent_cls(created):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.session_id = None
            self.resume_session = kwargs.get("resume_session")
            self.session_name = kwargs.get("session_name")
            self.permission_policy = SimpleNamespace(
                rule_store=object(), resolver=object()
            )
            self.run_calls = []
            self.shutdown_calls = 0
            created.append(self)

        def run(self, query, image_data=None):
            self.run_calls.append(query)
            if query == "interrupt":
                raise KeyboardInterrupt
            if query == "boom":
                raise RuntimeError("kaboom")
            self.session_id = 99

        def shutdown(self):
            self.shutdown_calls += 1

    return FakeAgent


def _wire(monkeypatch, created):
    monkeypatch.setattr("main.get_agent_class", lambda name: _fake_agent_cls(created))
    monkeypatch.setattr(
        "main.resolve_llm_config",
        lambda provider, model, llm_base, api_key: (
            llm_base or "http://base",
            api_key or "key",
            model or "model",
        ),
    )


def _repl(lines, created, monkeypatch, out, **arg_overrides):
    _wire(monkeypatch, created)
    return Repl(
        _args(**arg_overrides),
        permission_mode="allow",
        input_fn=_ScriptedInput(lines),
        output_fn=out.append,
    )


@pytest.mark.unit
class TestReplTurns:
    def test_turn_executes_and_quit_shuts_down_once(self, monkeypatch):
        created, out = [], []
        repl = _repl(["hello", "/quit"], created, monkeypatch, out)

        rc = repl.run()

        assert rc == 0
        assert repl.agent.run_calls == ["hello"]
        assert repl.agent.shutdown_calls == 1
        assert repl.resume_session == 99

    def test_finish_returns_to_prompt_not_exit(self, monkeypatch):
        created, out = [], []
        # A `finish` turn is a normal return from run(); the loop must continue.
        repl = _repl(["finish", "again", "/quit"], created, monkeypatch, out)

        repl.run()

        assert repl.agent.run_calls == ["finish", "again"]

    def test_keyboard_interrupt_returns_to_prompt(self, monkeypatch):
        created, out = [], []
        repl = _repl(["interrupt", "after", "/quit"], created, monkeypatch, out)

        repl.run()

        assert repl.agent.run_calls == ["interrupt", "after"]
        assert any("interrupted" in line.lower() for line in out)
        assert repl.agent.shutdown_calls == 1

    def test_turn_error_does_not_kill_repl(self, monkeypatch):
        created, out = [], []
        repl = _repl(["boom", "after", "/quit"], created, monkeypatch, out)

        repl.run()

        assert repl.agent.run_calls == ["boom", "after"]
        assert any("failed" in line.lower() for line in out)

    def test_eof_shuts_down_once(self, monkeypatch):
        created, out = [], []
        repl = _repl(["hi"], created, monkeypatch, out)

        rc = repl.run()

        assert rc == 0
        assert repl.agent.shutdown_calls == 1


@pytest.mark.unit
class TestReplMetaCommands:
    def test_help_lists_commands(self, monkeypatch):
        created, out = [], []
        repl = _repl(["/help", "/quit"], created, monkeypatch, out)

        repl.run()

        joined = "\n".join(out)
        for cmd in ("/quit", "/new", "/session", "/agents", "/provider", "/model"):
            assert cmd in joined

    def test_session_command_is_read_only(self, monkeypatch):
        created, out = [], []
        repl = _repl(["hi", "/session", "/quit"], created, monkeypatch, out)

        repl.run()

        assert any("Session ID: 99" in line for line in out)

    def test_new_resets_binding(self, monkeypatch):
        created, out = [], []
        repl = _repl(["hi", "/new", "/quit"], created, monkeypatch, out)

        repl.run()

        assert any("New session" in line for line in out)
        # After /new the binding is cleared until the next turn re-binds.
        assert repl.agent.session_id is None
        assert repl.resume_session is None

    def test_model_switch_preserves_session_binding(self, monkeypatch):
        created, out = [], []
        repl = _repl(["hi", "/model other-model", "/quit"], created, monkeypatch, out)

        repl.run()

        # A second agent instance was created for the switch...
        assert len(created) == 2
        # ...and it inherited the bound session rather than starting fresh.
        assert repl.agent is created[1]
        assert repl.agent.session_id == 99
        assert repl.model == "other-model"
        # The original agent is not shut down by the switch.
        assert created[0].shutdown_calls == 0

    def test_unknown_command_is_reported(self, monkeypatch):
        created, out = [], []
        repl = _repl(["/nope", "/quit"], created, monkeypatch, out)

        repl.run()

        assert any("Unknown command" in line for line in out)


@pytest.mark.unit
class TestReplStartup:
    def test_startup_failure_returns_error_without_shutdown(self, monkeypatch):
        out = []

        def boom(name):
            raise ValueError(f"Agent '{name}' not found.")

        monkeypatch.setattr("main.get_agent_class", boom)
        repl = Repl(
            _args(),
            permission_mode="allow",
            input_fn=_ScriptedInput(["/quit"]),
            output_fn=out.append,
        )

        rc = repl.run()

        assert rc == 1
        assert any("Could not start" in line for line in out)

    def test_run_repl_entrypoint(self, monkeypatch):
        created, out = [], []
        _wire(monkeypatch, created)

        rc = run_repl(
            _args(),
            permission_mode="allow",
            input_fn=_ScriptedInput(["hi", "/quit"]),
            output_fn=out.append,
        )

        assert rc == 0
        assert created[0].run_calls == ["hi"]

    def test_resume_session_passed_to_first_agent(self, monkeypatch):
        created, out = [], []
        repl = _repl(["/quit"], created, monkeypatch, out, resume_session=7)

        repl.run()

        # The first run() must resolve the resume target via init_session_db,
        # so the agent is not pre-bound to the ID.
        assert created[0].kwargs["resume_session"] == 7
        assert created[0].session_id is None

    def test_permission_mode_forwarded_to_agent(self, monkeypatch):
        from core.permissions import AutoDenyResolver

        created, out = [], []
        _wire(monkeypatch, created)
        repl = Repl(
            _args(),
            permission_mode="deny",
            input_fn=_ScriptedInput(["/quit"]),
            output_fn=out.append,
        )

        repl.run()

        assert isinstance(created[0].kwargs["permission_resolver"], AutoDenyResolver)
