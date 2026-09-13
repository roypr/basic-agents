import pytest

import main


def test_get_agent_class():
    assert main.get_agent_class("default")
    with pytest.raises(ValueError, match="ocr"):
        main.get_agent_class("ocr")


@pytest.mark.unit
class TestParserPermissionFlags:
    def _parse(self, argv):
        return main.build_parser().parse_args(argv)

    def test_chat_subcommand_registered(self):
        args = self._parse(["chat"])

        assert args.command == "chat"

    def test_run_interactive_flag(self):
        args = self._parse(["run", "--interactive"])

        assert args.interactive is True

    def test_run_defaults_to_allow_mode(self):
        args = self._parse(["run", "--query", "hi"])

        assert main._effective_permission_mode(args, interactive=False) == "allow"

    def test_chat_defaults_to_ask_mode(self):
        args = self._parse(["chat"])

        assert main._effective_permission_mode(args, interactive=True) == "ask"

    def test_explicit_mode_wins(self):
        args = self._parse(["chat", "--permission-mode", "deny"])

        assert main._effective_permission_mode(args, interactive=True) == "deny"

    def test_yes_overrides_to_allow(self):
        args = self._parse(["chat", "--permission-mode", "deny", "--yes"])

        assert main._effective_permission_mode(args, interactive=True) == "allow"

    def test_invalid_mode_rejected(self):
        with pytest.raises(SystemExit):
            self._parse(["chat", "--permission-mode", "nonsense"])
