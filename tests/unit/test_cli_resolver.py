import pytest

from cli.resolver import CliPermissionResolver, build_resolver
from core.permissions import (
    AutoAllowResolver,
    AutoDenyResolver,
    Decision,
    PermissionRequest,
)


class _ScriptedInput:
    """Returns queued lines; raises EOFError when exhausted."""

    def __init__(self, lines):
        self._lines = list(lines)

    def __call__(self, prompt_text=""):
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


def _resolver(lines, is_tty=True):
    out = []
    resolver = CliPermissionResolver(
        input_fn=_ScriptedInput(lines),
        output_fn=out.append,
        is_tty=lambda: is_tty,
    )
    return resolver, out


def _request(name="run_command"):
    return PermissionRequest(tool_name=name, args={"command": "ls"}, tool_call_id="c1")


@pytest.mark.unit
class TestCliResolverDecisions:
    @pytest.mark.parametrize(
        "answer,expected",
        [
            ("o", Decision.ALLOW_ONCE),
            ("a", Decision.ALLOW_ALWAYS),
            ("d", Decision.DENY_ONCE),
            ("n", Decision.DENY_ALWAYS),
        ],
    )
    def test_single_letter_choices(self, answer, expected):
        resolver, _ = _resolver([answer])

        outcome = resolver.resolve(_request())

        assert outcome.decision is expected
        assert outcome.allowed is (
            expected in (Decision.ALLOW_ONCE, Decision.ALLOW_ALWAYS)
        )
        assert outcome.message is None

    def test_deny_with_message(self):
        resolver, _ = _resolver(["m", "don't run that"])

        outcome = resolver.resolve(_request())

        assert outcome.allowed is False
        assert outcome.decision is Decision.DENY_WITH_MESSAGE
        assert outcome.message == "don't run that"

    def test_deny_with_blank_message_falls_back_to_deny_once(self):
        resolver, _ = _resolver(["m", ""])

        outcome = resolver.resolve(_request())

        assert outcome.decision is Decision.DENY_ONCE
        assert outcome.message is None

    def test_invalid_choice_reprompts(self):
        resolver, out = _resolver(["z", "o"])

        outcome = resolver.resolve(_request())

        assert outcome.decision is Decision.ALLOW_ONCE
        assert any("Invalid choice" in line for line in out)

    def test_eof_denies(self):
        resolver, _ = _resolver([])

        outcome = resolver.resolve(_request())

        assert outcome.allowed is False
        assert outcome.decision is Decision.DENY_ONCE


@pytest.mark.unit
class TestCliResolverSafety:
    def test_non_tty_fails_closed_without_prompting(self):
        consumed = []
        resolver = CliPermissionResolver(
            input_fn=lambda prompt_text="": consumed.append(prompt_text),
            output_fn=lambda *a, **k: None,
            is_tty=lambda: False,
        )

        outcome = resolver.resolve(_request())

        assert outcome.allowed is False
        assert outcome.decision is Decision.DENY_ONCE
        # Nobody should have been prompted.
        assert consumed == []


@pytest.mark.unit
class TestBuildResolver:
    def test_allow_mode(self):
        assert isinstance(build_resolver("allow"), AutoAllowResolver)

    def test_deny_mode(self):
        assert isinstance(build_resolver("deny"), AutoDenyResolver)

    def test_ask_mode(self):
        assert isinstance(build_resolver("ask"), CliPermissionResolver)

    def test_unknown_mode_defaults_to_interactive(self):
        assert isinstance(build_resolver(None), CliPermissionResolver)
