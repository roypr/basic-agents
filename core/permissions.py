"""Permission gate core.

Pure, testable policy engine: decision types, request/outcome shapes, the
resolver protocol, and the per-agent grading policy.

This module performs no I/O and knows nothing about the terminal. The CLI
layer supplies a ``PermissionResolver``; tests supply the auto resolvers
defined here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class Decision(Enum):
    """The user's answer to a permission prompt."""

    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    DENY_ONCE = "deny_once"
    DENY_ALWAYS = "deny_always"
    DENY_WITH_MESSAGE = "deny_with_message"


# Tools that are safe to execute without prompting. Hardcoded (not derived from
# tool_definition.json) so inline tools such as ``tree_sitter_tags`` are covered.
# Any tool an agent exposes that is *not* in this set defaults to ASK
# (fail-closed). See readme.md for the agent authoring guidance.
SAFE_TOOLS = frozenset(
    {
        "get_current_date",
        "glob_search",
        "grep_search",
        "get_all_files",
        "read_lines",
        "file_read",
        "read_image",
        "tree_sitter_tags",
        "web_search",
        "request_get",
        "finish",
    }
)

# Only these decisions are standing rules and therefore persisted to the store.
# ``DENY_WITH_MESSAGE`` is a one-time redirect and must never be persisted.
_PERSISTED_DECISIONS = (Decision.ALLOW_ALWAYS, Decision.DENY_ALWAYS)

HALLUCINATED_TOOL_TEMPLATE = (
    "Error: unknown tool '{tool_name}' — not registered for this agent"
)


@dataclass
class PermissionRequest:
    """A gated tool call awaiting a decision from a resolver."""

    tool_name: str
    args: dict
    tool_call_id: str | None = None


@dataclass
class PermissionOutcome:
    """The policy's verdict for a single tool call.

    ``error`` is True for hallucinated tools (not registered for this agent):
    the call must be answered with an error tool result, not a denial prompt.
    """

    allowed: bool
    message: str | None
    decision: Decision
    error: bool = False


@runtime_checkable
class PermissionResolver(Protocol):
    """The seam between the agent loop and the UI.

    ``base_agent`` never calls ``input()``; it delegates unresolved gated tools
    to an injected resolver.
    """

    def resolve(self, request: PermissionRequest) -> PermissionOutcome: ...


class AutoAllowResolver:
    """Non-interactive resolver that always allows once."""

    def resolve(self, request: PermissionRequest) -> PermissionOutcome:
        return PermissionOutcome(
            allowed=True, message=None, decision=Decision.ALLOW_ONCE
        )


class AutoDenyResolver:
    """Non-interactive resolver that always denies once (fail-closed)."""

    def resolve(self, request: PermissionRequest) -> PermissionOutcome:
        return PermissionOutcome(
            allowed=False, message=None, decision=Decision.DENY_ONCE
        )


class PermissionPolicy:
    """Grades tool calls for a single agent and resolves gated ones.

    Grading is per-agent: the policy is constructed with the agent's actual
    tool names (the keys of ``tool_map``), so the fail-closed default only
    fires for tools the agent actually exposes.
    """

    def __init__(
        self,
        agent_tool_names,
        rule_store,
        resolver,
        safe_tools=None,
        scope: str = "session",
        scope_key=None,
    ):
        self.agent_tool_names = set(agent_tool_names)
        self.rule_store = rule_store
        self.resolver = resolver
        self.safe_tools = set(SAFE_TOOLS if safe_tools is None else safe_tools)
        self.scope = scope
        self.scope_key = scope_key

    def check(
        self, tool_name: str, args: dict, tool_call_id: str | None = None
    ) -> PermissionOutcome:
        # Hallucinated tool: not registered for this agent. Fail fast with an
        # error result instead of prompting for a tool that cannot run.
        if tool_name not in self.agent_tool_names:
            return PermissionOutcome(
                allowed=False,
                message=HALLUCINATED_TOOL_TEMPLATE.format(tool_name=tool_name),
                decision=Decision.DENY_ONCE,
                error=True,
            )

        # Safe tool: allow silently, no prompt and no rule write.
        if tool_name in self.safe_tools:
            return PermissionOutcome(
                allowed=True, message=None, decision=Decision.ALLOW_ONCE
            )

        # Gated tool: honor a standing rule if one exists.
        rule = self.rule_store.get(self.scope, self.scope_key, tool_name)
        if rule is not None:
            return PermissionOutcome(
                allowed=rule is Decision.ALLOW_ALWAYS,
                message=None,
                decision=rule,
            )

        # Miss: ask the injected resolver.
        request = PermissionRequest(
            tool_name=tool_name, args=args, tool_call_id=tool_call_id
        )
        outcome = self.resolver.resolve(request)

        # Persist standing rules only; never persist a one-time redirect.
        if outcome.decision in _PERSISTED_DECISIONS:
            self.rule_store.set(self.scope, self.scope_key, tool_name, outcome.decision)
        return outcome
