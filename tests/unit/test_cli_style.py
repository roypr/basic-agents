import pytest

from cli import style


class _FakeStream:
    """Minimal stream stand-in exposing just ``isatty``."""

    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


@pytest.mark.unit
class TestSupportsColor:
    def test_true_for_tty(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        assert style._supports_color(_FakeStream(True)) is True

    def test_false_for_non_tty(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        assert style._supports_color(_FakeStream(False)) is False

    def test_no_color_env_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert style._supports_color(_FakeStream(True)) is False

    def test_dumb_term_disables(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        assert style._supports_color(_FakeStream(True)) is False

    def test_missing_isatty_fails_closed(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        assert style._supports_color(object()) is False


@pytest.mark.unit
class TestHelpers:
    def test_disabled_returns_text_unchanged(self, monkeypatch):
        monkeypatch.setattr(style, "_supports_color", lambda *a, **k: False)
        assert style.bold("x") == "x"
        assert style.red("x") == "x"
        assert style.style("x", style._RED) == "x"

    def test_enabled_wraps_with_reset(self, monkeypatch):
        monkeypatch.setattr(style, "_supports_color", lambda *a, **k: True)
        assert style.bold("x") == f"{style._BOLD}x{style._RESET}"
        assert style.cyan("x") == f"{style._CYAN}x{style._RESET}"

    def test_style_without_codes_is_plain(self, monkeypatch):
        monkeypatch.setattr(style, "_supports_color", lambda *a, **k: True)
        assert style.style("x") == "x"

    def test_combined_codes(self, monkeypatch):
        monkeypatch.setattr(style, "_supports_color", lambda *a, **k: True)
        assert (
            style.style("x", style._BOLD, style._CYAN)
            == f"{style._BOLD}{style._CYAN}x{style._RESET}"
        )
