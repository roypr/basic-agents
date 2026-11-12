import pytest

import main
from main import build_parser, do_mcp, resolve_mcp_config


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

    def test_generation_flags_parse(self):
        args = self._parse(
            [
                "run",
                "--max-output-tokens",
                "4096",
                "--reasoning-effort",
                "high",
            ]
        )

        assert args.max_output_tokens == 4096
        assert args.reasoning_effort == "high"


@pytest.mark.unit
class TestMcpCommand:
    def test_mcp_list_registered(self):
        args = build_parser().parse_args(["mcp", "list"])

        assert args.command == "mcp"
        assert args.mcp_command == "list"
        assert args.no_mcp is False

    def test_mcp_list_flags(self):
        args = build_parser().parse_args(
            ["mcp", "list", "--mcp-config", "custom.json", "--no-mcp"]
        )

        assert args.mcp_config == "custom.json"
        assert args.no_mcp is True

    def test_do_mcp_without_config_is_a_no_op(self, capsys):
        args = build_parser().parse_args(["mcp", "list", "--no-mcp"])

        do_mcp(args)

        assert "No MCP servers configured" in capsys.readouterr().out

    def test_do_mcp_missing_explicit_config_exits(self, monkeypatch):
        # The suite-wide kill switch is bypassed so the explicit path is read.
        monkeypatch.delenv("BASIC_AGENTS_NO_MCP", raising=False)
        args = build_parser().parse_args(["mcp", "list", "--mcp-config", "nope.json"])

        with pytest.raises(SystemExit) as excinfo:
            do_mcp(args)

        assert excinfo.value.code == 1

    def test_do_mcp_prints_rows(self, monkeypatch, capsys):
        from utils.mcp import McpConfig, McpServerConfig

        monkeypatch.setattr(
            "main.resolve_mcp_config",
            lambda args: McpConfig(enabled=True, servers=(McpServerConfig(name="fs", command="x"),)),
        )
        monkeypatch.setattr(
            "main.describe_servers",
            lambda config: [
                {
                    "name": "fs",
                    "transport": "stdio",
                    "target": "npx server",
                    "agents": "all",
                    "tools": 3,
                    "resources": True,
                    "prompts": False,
                    "error": None,
                }
            ],
        )
        monkeypatch.setattr("main.close_mcp_servers", lambda: None)

        do_mcp(build_parser().parse_args(["mcp", "list"]))

        out = capsys.readouterr().out
        assert "[fs] stdio -> npx server (3 tools, agents: all) [resources]" in out

    def test_resolve_mcp_config_no_mcp_disables(self):
        args = build_parser().parse_args(["run", "--no-mcp"])

        resolved = resolve_mcp_config(args)

        assert resolved.enabled is False
