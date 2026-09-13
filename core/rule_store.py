"""Storage for standing permission rules.

The protocol exists now so a SQLite-backed store can drop in later without
touching callers. For v1 the scope is hardcoded to ``"session"`` and the
``scope_key`` is the session ID.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.permissions import Decision


@runtime_checkable
class RuleStore(Protocol):
    """Keyed by ``(scope, scope_key, tool_name)`` -> standing ``Decision``."""

    def get(self, scope: str, scope_key, tool_name: str) -> Decision | None: ...

    def set(
        self, scope: str, scope_key, tool_name: str, decision: Decision
    ) -> None: ...


class InMemoryRuleStore:
    """Process-lifetime rule store. Rules reset on restart (by design, v1)."""

    def __init__(self):
        self._rules: dict[tuple, Decision] = {}

    def get(self, scope: str, scope_key, tool_name: str) -> Decision | None:
        return self._rules.get((scope, scope_key, tool_name))

    def set(self, scope: str, scope_key, tool_name: str, decision: Decision) -> None:
        self._rules[(scope, scope_key, tool_name)] = decision
