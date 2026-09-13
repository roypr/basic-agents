import pytest

from core.permissions import (
    SAFE_TOOLS,
    AutoAllowResolver,
    AutoDenyResolver,
    Decision,
    PermissionOutcome,
    PermissionPolicy,
    PermissionRequest,
)
from core.rule_store import InMemoryRuleStore


class ScriptedResolver:
    """Test resolver that always returns a fixed outcome and records requests."""

    def __init__(self, outcome: PermissionOutcome):
        self.outcome = outcome
        self.requests: list[PermissionRequest] = []

    def resolve(self, request: PermissionRequest) -> PermissionOutcome:
        self.requests.append(request)
        return self.outcome


def make_policy(agent_tools, resolver=None, store=None, scope_key=1):
    return PermissionPolicy(
        agent_tool_names=agent_tools,
        rule_store=store if store is not None else InMemoryRuleStore(),
        resolver=resolver if resolver is not None else AutoAllowResolver(),
        scope_key=scope_key,
    )


@pytest.mark.unit
class TestGradingResolution:
    def test_safe_tool_allowed_without_prompting(self):
        resolver = ScriptedResolver(
            PermissionOutcome(False, "nope", Decision.DENY_ONCE)
        )
        policy = make_policy(["file_read"], resolver=resolver)

        outcome = policy.check("file_read", {"path": "a.txt"})

        assert outcome.allowed is True
        assert outcome.decision is Decision.ALLOW_ONCE
        assert outcome.message is None
        assert outcome.error is False
        assert resolver.requests == []

    def test_gated_tool_delegates_to_resolver(self):
        resolver = ScriptedResolver(PermissionOutcome(True, None, Decision.ALLOW_ONCE))
        policy = make_policy(["file_write"], resolver=resolver)

        outcome = policy.check("file_write", {"path": "a.txt"}, tool_call_id="call_1")

        assert outcome.allowed is True
        assert len(resolver.requests) == 1
        assert resolver.requests[0].tool_name == "file_write"
        assert resolver.requests[0].args == {"path": "a.txt"}
        assert resolver.requests[0].tool_call_id == "call_1"

    def test_hallucinated_tool_produces_error_not_prompt(self):
        resolver = ScriptedResolver(PermissionOutcome(True, None, Decision.ALLOW_ONCE))
        policy = make_policy(["get_current_date"], resolver=resolver)

        outcome = policy.check("run_command", {"command": "ls"})

        assert outcome.allowed is False
        assert outcome.error is True
        assert "run_command" in outcome.message
        assert "not registered" in outcome.message
        assert resolver.requests == []

    def test_safe_named_tool_absent_from_agent_is_error(self):
        # web_search is in SAFE_TOOLS but this agent doesn't expose it — the
        # policy must fail fast rather than auto-allow a tool that can't run.
        policy = make_policy(["get_current_date"])

        outcome = policy.check("web_search", {"query": "x"})

        assert outcome.error is True
        assert outcome.allowed is False


@pytest.mark.unit
class TestDecisionTypes:
    def test_allow_once(self):
        resolver = ScriptedResolver(PermissionOutcome(True, None, Decision.ALLOW_ONCE))
        store = InMemoryRuleStore()
        policy = make_policy(["file_write"], resolver=resolver, store=store)

        outcome = policy.check("file_write", {})

        assert outcome.allowed is True
        assert outcome.decision is Decision.ALLOW_ONCE
        assert store.get("session", 1, "file_write") is None

    def test_allow_always_is_persisted(self):
        resolver = ScriptedResolver(
            PermissionOutcome(True, None, Decision.ALLOW_ALWAYS)
        )
        store = InMemoryRuleStore()
        policy = make_policy(
            ["file_write"], resolver=resolver, store=store, scope_key=42
        )

        outcome = policy.check("file_write", {})

        assert outcome.allowed is True
        assert outcome.decision is Decision.ALLOW_ALWAYS
        assert store.get("session", 42, "file_write") is Decision.ALLOW_ALWAYS

    def test_deny_once_is_not_persisted(self):
        resolver = ScriptedResolver(PermissionOutcome(False, None, Decision.DENY_ONCE))
        store = InMemoryRuleStore()
        policy = make_policy(
            ["file_write"], resolver=resolver, store=store, scope_key=42
        )

        outcome = policy.check("file_write", {})

        assert outcome.allowed is False
        assert outcome.decision is Decision.DENY_ONCE
        assert store.get("session", 42, "file_write") is None

    def test_deny_always_is_persisted(self):
        resolver = ScriptedResolver(
            PermissionOutcome(False, None, Decision.DENY_ALWAYS)
        )
        store = InMemoryRuleStore()
        policy = make_policy(
            ["file_write"], resolver=resolver, store=store, scope_key=42
        )

        outcome = policy.check("file_write", {})

        assert outcome.allowed is False
        assert outcome.decision is Decision.DENY_ALWAYS
        assert store.get("session", 42, "file_write") is Decision.DENY_ALWAYS

    def test_deny_with_message_is_never_persisted(self):
        resolver = ScriptedResolver(
            PermissionOutcome(
                False, "use the config file instead", Decision.DENY_WITH_MESSAGE
            )
        )
        store = InMemoryRuleStore()
        policy = make_policy(
            ["file_write"], resolver=resolver, store=store, scope_key=42
        )

        outcome = policy.check("file_write", {})

        assert outcome.allowed is False
        assert outcome.message == "use the config file instead"
        assert outcome.decision is Decision.DENY_WITH_MESSAGE
        assert store.get("session", 42, "file_write") is None


@pytest.mark.unit
class TestRuleStoreIntegration:
    def test_hit_short_circuits_resolver(self):
        store = InMemoryRuleStore()
        store.set("session", 7, "file_write", Decision.ALLOW_ALWAYS)
        resolver = ScriptedResolver(PermissionOutcome(False, None, Decision.DENY_ONCE))
        policy = make_policy(
            ["file_write"], resolver=resolver, store=store, scope_key=7
        )

        outcome = policy.check("file_write", {})

        assert outcome.allowed is True
        assert outcome.decision is Decision.ALLOW_ALWAYS
        assert resolver.requests == []

    def test_deny_always_rule_denies_without_prompting(self):
        store = InMemoryRuleStore()
        store.set("session", 7, "run_command", Decision.DENY_ALWAYS)
        resolver = ScriptedResolver(PermissionOutcome(True, None, Decision.ALLOW_ONCE))
        policy = make_policy(
            ["run_command"], resolver=resolver, store=store, scope_key=7
        )

        outcome = policy.check("run_command", {})

        assert outcome.allowed is False
        assert outcome.decision is Decision.DENY_ALWAYS
        assert resolver.requests == []

    def test_rule_is_scoped_by_session_key(self):
        store = InMemoryRuleStore()
        store.set("session", 7, "file_write", Decision.ALLOW_ALWAYS)
        resolver = ScriptedResolver(PermissionOutcome(False, None, Decision.DENY_ONCE))
        policy = make_policy(
            ["file_write"], resolver=resolver, store=store, scope_key=99
        )

        outcome = policy.check("file_write", {})

        # Different session -> rule miss -> resolver consulted.
        assert outcome.allowed is False
        assert resolver.requests[0].tool_name == "file_write"


@pytest.mark.unit
class TestAutoResolvers:
    def test_auto_allow(self):
        outcome = AutoAllowResolver().resolve(PermissionRequest("t", {}))

        assert outcome.allowed is True
        assert outcome.decision is Decision.ALLOW_ONCE
        assert outcome.message is None

    def test_auto_deny(self):
        outcome = AutoDenyResolver().resolve(PermissionRequest("t", {}))

        assert outcome.allowed is False
        assert outcome.decision is Decision.DENY_ONCE
        assert outcome.message is None


@pytest.mark.unit
class TestSafeTools:
    def test_inline_tool_is_included(self):
        # tree_sitter_tags is defined inline in agents/code/tools.py, not in
        # tool_definition.json — the hardcoded set must still cover it.
        assert "tree_sitter_tags" in SAFE_TOOLS

    def test_side_effecting_tools_are_not_safe(self):
        for tool in (
            "file_write",
            "file_edit",
            "remove_lines",
            "replace_lines",
            "file_delete",
            "run_command",
        ):
            assert tool not in SAFE_TOOLS
