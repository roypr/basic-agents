import json
from unittest.mock import Mock

import pytest

from core.base_agent import BaseAgent
from core.permissions import (
    AutoAllowResolver,
    Decision,
    PermissionOutcome,
    PermissionPolicy,
)
from core.rule_store import InMemoryRuleStore
from db.session_db import SessionDB


def _tc(name: str, args: dict, call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class MapResolver:
    """Resolver returning a per-tool-name outcome and recording prompts."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.requests: list[str] = []

    def resolve(self, request):
        self.requests.append(request.tool_name)
        return self.mapping[request.tool_name]


class _FakeAdapter:
    def extract_tool_calls(self, msg):
        return msg.get("tool_calls")

    def extract_reasoning(self, msg):
        return None

    def extract_content(self, msg):
        return msg.get("content")


def _bare_agent(tool_map, policy):
    """Build an agent without running __init__ side effects (DB, adapter)."""
    agent = BaseAgent.__new__(BaseAgent)
    agent.name = "TestAgent"
    agent.tool_map = tool_map
    agent.tools = list(tool_map.keys())
    agent.permission_policy = policy
    return agent


def _run_agent():
    """Build an agent wired for a full run() against mocked DB/LLM."""
    agent = BaseAgent.__new__(BaseAgent)
    agent.name = "TestAgent"
    agent.model = "local"
    agent.llm_base = "http://localhost"
    agent.max_turns = 1
    agent.resume_session = None
    agent.session_name = "Test Session"
    agent.api_key = ""
    agent.use_tools = True
    agent.stream_sink = None
    agent._shutdown_requested = False
    agent.session_id = None
    agent.adapter = _FakeAdapter()
    agent.session_db = Mock()
    agent.session_db.get_messages.return_value = []
    agent.tools = []
    agent.tool_map = {}
    agent.system_prompt = "sys"
    agent.permission_policy = PermissionPolicy(
        agent_tool_names=[],
        rule_store=InMemoryRuleStore(),
        resolver=AutoAllowResolver(),
    )
    return agent


@pytest.mark.unit
class TestGateAndMerge:
    def test_gate_runs_before_execute_with_only_approved(self, monkeypatch):
        captured = []

        def fake_execute(tool_calls, tool_map, tools):
            captured.append([tc["function"]["name"] for tc in tool_calls])
            return [
                (tc, tc["function"]["name"], {}, f"ran {tc['function']['name']}")
                for tc in tool_calls
            ]

        monkeypatch.setattr("core.base_agent.execute_tool_calls", fake_execute)

        resolver = MapResolver(
            {
                "file_write": PermissionOutcome(True, None, Decision.ALLOW_ONCE),
                "run_command": PermissionOutcome(False, None, Decision.DENY_ONCE),
            }
        )
        tool_map = {
            "file_read": lambda **kwargs: "",
            "file_write": lambda **kwargs: "",
            "run_command": lambda **kwargs: "",
        }
        policy = PermissionPolicy(
            agent_tool_names=tool_map.keys(),
            rule_store=InMemoryRuleStore(),
            resolver=resolver,
        )
        agent = _bare_agent(tool_map, policy)

        tool_calls = [
            _tc("file_read", {"path": "a"}, "c0"),
            _tc("file_write", {"path": "b"}, "c1"),
            _tc("run_command", {"command": "rm -rf /"}, "c2"),
            _tc("delete_everything", {}, "c3"),
        ]

        results = agent._gate_and_execute(tool_calls)

        # Executor only ever saw the approved calls, in order.
        assert captured == [["file_read", "file_write"]]
        # Safe and hallucinated tools were never prompted.
        assert resolver.requests == ["file_write", "run_command"]
        # Every original call is accounted for exactly once.
        assert len(results) == 4

    def test_results_merged_in_original_tool_call_order(self, monkeypatch):
        monkeypatch.setattr(
            "core.base_agent.execute_tool_calls",
            lambda tool_calls, tool_map, tools: [
                (tc, tc["function"]["name"], {}, f"ran {tc['function']['name']}")
                for tc in tool_calls
            ],
        )

        resolver = MapResolver(
            {
                "file_write": PermissionOutcome(True, None, Decision.ALLOW_ONCE),
                "run_command": PermissionOutcome(False, None, Decision.DENY_ONCE),
            }
        )
        tool_map = {
            "file_read": lambda **kwargs: "",
            "file_write": lambda **kwargs: "",
            "run_command": lambda **kwargs: "",
        }
        policy = PermissionPolicy(
            agent_tool_names=tool_map.keys(),
            rule_store=InMemoryRuleStore(),
            resolver=resolver,
        )
        agent = _bare_agent(tool_map, policy)

        tool_calls = [
            _tc("file_read", {"path": "a"}, "c0"),
            _tc("file_write", {"path": "b"}, "c1"),
            _tc("run_command", {"command": "x"}, "c2"),
            _tc("delete_everything", {}, "c3"),
        ]

        results = agent._gate_and_execute(tool_calls)

        assert [r[0]["id"] for r in results] == ["c0", "c1", "c2", "c3"]
        assert [r[1] for r in results] == [
            "file_read",
            "file_write",
            "run_command",
            "delete_everything",
        ]
        assert results[0][3] == "ran file_read"
        assert results[1][3] == "ran file_write"
        assert results[2][3] == (
            "Permission denied by user. Do not retry this tool unless explicitly asked."
        )
        assert results[3][3] == (
            "Error: unknown tool 'delete_everything' — not registered for this agent"
        )

    def test_denial_with_message_text(self, monkeypatch):
        monkeypatch.setattr(
            "core.base_agent.execute_tool_calls", lambda *args, **kwargs: []
        )

        resolver = MapResolver(
            {
                "run_command": PermissionOutcome(
                    False, "don't do that", Decision.DENY_WITH_MESSAGE
                )
            }
        )
        tool_map = {"run_command": lambda **kwargs: ""}
        policy = PermissionPolicy(
            agent_tool_names=tool_map.keys(),
            rule_store=InMemoryRuleStore(),
            resolver=resolver,
        )
        agent = _bare_agent(tool_map, policy)

        results = agent._gate_and_execute(
            [_tc("run_command", {"command": "rm -rf /"}, "c0")]
        )

        assert results[0][3] == (
            'Permission denied by user. The user says: "don\'t do that". '
            "Do not retry this tool unless explicitly asked."
        )

    def test_all_denied_never_invokes_executor(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError("executor must not run when nothing is approved")

        monkeypatch.setattr("core.base_agent.execute_tool_calls", boom)

        resolver = MapResolver(
            {"file_write": PermissionOutcome(False, None, Decision.DENY_ONCE)}
        )
        tool_map = {"file_write": lambda **kwargs: ""}
        policy = PermissionPolicy(
            agent_tool_names=tool_map.keys(),
            rule_store=InMemoryRuleStore(),
            resolver=resolver,
        )
        agent = _bare_agent(tool_map, policy)

        results = agent._gate_and_execute([_tc("file_write", {"path": "x"}, "c0")])

        assert results[0][3].startswith("Permission denied by user.")


@pytest.mark.unit
class TestOrphanRecovery:
    def test_orphaned_tool_calls_are_recovered_and_persisted(self, tmp_path):
        db = SessionDB(str(tmp_path / "sessions.db"))
        session_id = db.create_session("S")
        db.add_message(session_id, "user", "hi")
        db.add_message(
            session_id,
            "assistant",
            "",
            tool_calls=[_tc("file_write", {"path": "x"}, "call_orphan")],
        )

        agent = BaseAgent.__new__(BaseAgent)
        agent.session_db = db

        messages = db.get_messages(session_id)
        recovered = agent._recover_orphaned_tool_calls(session_id, messages)

        assert any(
            m["role"] == "tool" and m.get("tool_call_id") == "call_orphan"
            for m in recovered
        )

        # Persisted: reloading the session shows the synthesized tool row.
        reloaded = db.get_messages(session_id)
        tool_rows = [m for m in reloaded if m["role"] == "tool"]
        assert len(tool_rows) == 1
        assert tool_rows[0]["tool_call_id"] == "call_orphan"
        assert "interrupted" in tool_rows[0]["content"]

    def test_recovery_is_idempotent(self, tmp_path):
        db = SessionDB(str(tmp_path / "sessions.db"))
        session_id = db.create_session("S")
        db.add_message(
            session_id,
            "assistant",
            "",
            tool_calls=[_tc("file_write", {"path": "x"}, "call_orphan")],
        )

        agent = BaseAgent.__new__(BaseAgent)
        agent.session_db = db

        agent._recover_orphaned_tool_calls(session_id, db.get_messages(session_id))
        agent._recover_orphaned_tool_calls(session_id, db.get_messages(session_id))

        tool_rows = [m for m in db.get_messages(session_id) if m["role"] == "tool"]
        assert len(tool_rows) == 1

    def test_answered_tool_calls_are_left_alone(self, tmp_path):
        db = SessionDB(str(tmp_path / "sessions.db"))
        session_id = db.create_session("S")
        db.add_message(
            session_id,
            "assistant",
            "",
            tool_calls=[_tc("file_read", {"path": "x"}, "call_done")],
        )
        db.add_message(session_id, "tool", "already ran", tool_call_id="call_done")

        agent = BaseAgent.__new__(BaseAgent)
        agent.session_db = db

        agent._recover_orphaned_tool_calls(session_id, db.get_messages(session_id))

        tool_rows = [m for m in db.get_messages(session_id) if m["role"] == "tool"]
        assert len(tool_rows) == 1
        assert tool_rows[0]["content"] == "already ran"


@pytest.mark.unit
class TestSessionBinding:
    def test_run_binds_session_once_and_reuses_it(self, monkeypatch):
        init_calls = []

        def fake_init(resume, name, prompt, agent_name=""):
            init_calls.append(resume)
            return 55

        monkeypatch.setattr("core.base_agent.init_session_db", fake_init)
        monkeypatch.setattr(
            "core.base_agent.call_llm_streaming",
            lambda *args, **kwargs: {"role": "assistant", "content": "ok"},
        )

        agent = _run_agent()
        agent.run("first")
        agent.run("second")

        assert agent.session_id == 55
        assert len(init_calls) == 1

    def test_new_reset_rebinds_on_next_run(self, monkeypatch):
        init_calls = []

        def fake_init(resume, name, prompt, agent_name=""):
            init_calls.append(resume)
            return 100 + len(init_calls)

        monkeypatch.setattr("core.base_agent.init_session_db", fake_init)
        monkeypatch.setattr(
            "core.base_agent.call_llm_streaming",
            lambda *args, **kwargs: {"role": "assistant", "content": "ok"},
        )

        agent = _run_agent()
        agent.run("first")
        assert agent.session_id == 101

        # Simulate the /new meta-command reset.
        agent.session_id = None
        agent.resume_session = None
        agent.run("second")

        assert agent.session_id == 102
        assert len(init_calls) == 2

    def test_policy_scope_key_follows_bound_session(self, monkeypatch):
        monkeypatch.setattr(
            "core.base_agent.init_session_db",
            lambda resume, name, prompt, agent_name="": 77,
        )
        monkeypatch.setattr(
            "core.base_agent.call_llm_streaming",
            lambda *args, **kwargs: {"role": "assistant", "content": "ok"},
        )

        agent = _run_agent()
        agent.run("hi")

        assert agent.permission_policy.scope_key == 77


@pytest.mark.unit
class TestStreamSink:
    def test_run_forwards_stream_sink_to_llm(self, monkeypatch):
        captured = {}

        monkeypatch.setattr(
            "core.base_agent.init_session_db",
            lambda resume, name, prompt, agent_name="": 5,
        )

        def fake_llm(*args, **kwargs):
            captured.update(kwargs)
            return {"role": "assistant", "content": "ok"}

        monkeypatch.setattr("core.base_agent.call_llm_streaming", fake_llm)

        sink = object()
        agent = _run_agent()
        agent.stream_sink = sink
        agent.run("hi")

        assert captured["sink"] is sink
