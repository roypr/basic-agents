"""mcp.json parsing: validation, ${ENV} expansion, per-server filters."""

import json
import os

import pytest

from utils.mcp import config as mcp_config
from utils.mcp.config import (
    DISABLE_ENV_VAR,
    McpConfig,
    load_mcp_config,
    parse_mcp_config,
)
from utils.mcp.errors import McpConfigError, McpError


@pytest.fixture(autouse=True)
def _mcp_allowed(monkeypatch):
    """The suite-wide guard disables MCP; config tests exercise the loader."""
    monkeypatch.delenv(DISABLE_ENV_VAR, raising=False)


def _write(tmp_path, data):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _server(**overrides):
    server = {"transport": "stdio", "command": "npx", "args": ["-y", "server"]}
    server.update(overrides)
    return server


@pytest.mark.unit
class TestLoading:
    def test_missing_default_file_means_mcp_is_off(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_config, "DEFAULT_CONFIG_PATH", tmp_path / "mcp.json")

        config = load_mcp_config()

        assert config.enabled is False
        assert config.servers == ()

    def test_explicit_missing_path_is_an_error(self, tmp_path):
        with pytest.raises(McpConfigError) as excinfo:
            load_mcp_config(tmp_path / "nope.json")

        assert "not found" in str(excinfo.value)

    def test_invalid_json_reports_the_path(self, tmp_path):
        path = tmp_path / "mcp.json"
        path.write_text("{ nope", encoding="utf-8")

        with pytest.raises(McpConfigError) as excinfo:
            load_mcp_config(path)

        assert "not valid JSON" in str(excinfo.value)

    def test_valid_file_is_parsed(self, tmp_path):
        path = _write(tmp_path, {"servers": {"fs": _server()}})

        config = load_mcp_config(path)

        assert config.enabled is True
        assert config.servers[0].name == "fs"
        assert config.servers[0].timeout == 60.0
        assert config.servers[0].inherit_env is True

    def test_kill_switch_env_var(self, monkeypatch, tmp_path):
        path = _write(tmp_path, {"servers": {"fs": _server()}})
        monkeypatch.setenv(DISABLE_ENV_VAR, "1")

        assert load_mcp_config(path).enabled is False

    def test_enabled_false_and_empty_servers(self, tmp_path):
        assert (
            load_mcp_config(
                _write(tmp_path, {"enabled": False, "servers": {"a": _server()}})
            ).enabled
            is False
        )
        assert load_mcp_config(_write(tmp_path, {"servers": {}})).enabled is False


@pytest.mark.unit
class TestValidation:
    def test_unknown_transport_is_rejected(self):
        with pytest.raises(McpConfigError) as excinfo:
            parse_mcp_config(
                {"servers": {"gh": {"transport": "websocket", "url": "https://x"}}}
            )

        assert "not supported" in str(excinfo.value)
        assert "stdio" in str(excinfo.value)

    @pytest.mark.parametrize(
        "transport", ["http", "streamable-http", "streamable_http", "streamablehttp"]
    )
    def test_http_transport_aliases_need_a_url(self, transport):
        config = parse_mcp_config(
            {
                "servers": {
                    "gh": {
                        "transport": transport,
                        "url": "https://api.example.com/mcp",
                        "headers": {"Authorization": "Bearer t"},
                    }
                }
            }
        )

        server = config.servers[0]
        assert server.transport == "http"
        assert server.url == "https://api.example.com/mcp"
        assert server.headers == {"Authorization": "Bearer t"}
        assert server.command == ""

    def test_http_without_url_is_an_error(self):
        with pytest.raises(McpConfigError) as excinfo:
            parse_mcp_config({"servers": {"gh": {"transport": "http"}}})

        assert "needs a 'url'" in str(excinfo.value)

    def test_url_must_be_http_or_https(self):
        with pytest.raises(McpConfigError):
            parse_mcp_config(
                {"servers": {"gh": {"transport": "http", "url": "api.example.com"}}}
            )

    def test_sse_transport_is_accepted(self):
        config = parse_mcp_config(
            {"servers": {"legacy": {"transport": "sse", "url": "https://x/sse"}}}
        )

        assert config.servers[0].transport == "sse"

    def test_headers_must_be_strings(self):
        with pytest.raises(McpConfigError):
            parse_mcp_config(
                {
                    "servers": {
                        "gh": {
                            "transport": "http",
                            "url": "https://x/mcp",
                            "headers": {"Authorization": 1},
                        }
                    }
                }
            )

    def test_resource_and_prompt_exposure_flags(self):
        config = parse_mcp_config(
            {"servers": {"fs": _server(expose_resources=True, expose_prompts=True)}}
        )

        assert config.servers[0].expose_resources is True
        assert config.servers[0].expose_prompts is True

    def test_exposure_is_off_by_default(self):
        config = parse_mcp_config({"servers": {"fs": _server()}})

        assert config.servers[0].expose_resources is False
        assert config.servers[0].expose_prompts is False

    def test_stdio_requires_a_command(self):
        with pytest.raises(McpConfigError) as excinfo:
            parse_mcp_config({"servers": {"fs": {"transport": "stdio"}}})

        assert "needs a 'command'" in str(excinfo.value)

    def test_args_must_be_strings(self):
        with pytest.raises(McpConfigError):
            parse_mcp_config({"servers": {"fs": _server(args=[1, 2])}})

    def test_timeout_must_be_positive(self):
        with pytest.raises(McpConfigError):
            parse_mcp_config({"servers": {"fs": _server(timeout=0)}})

    def test_servers_must_be_an_object(self):
        with pytest.raises(McpConfigError):
            parse_mcp_config({"servers": ["fs"]})


@pytest.mark.unit
class TestEnvExpansion:
    def test_expands_in_env_args_and_command(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "s3cret")
        monkeypatch.delenv("MISSING_ONE", raising=False)

        config = parse_mcp_config(
            {
                "servers": {
                    "fs": _server(
                        command="${MISSING_ONE:-npx}",
                        args=["--token", "${MY_TOKEN}"],
                        env={"TOKEN": "${MY_TOKEN}", "FALLBACK": "${MISSING_ONE:-x}"},
                    )
                }
            }
        )

        server = config.servers[0]
        assert server.command == "npx"
        assert server.args == ("--token", "s3cret")
        assert server.env == {"TOKEN": "s3cret", "FALLBACK": "x"}

    def test_missing_variable_without_default_is_an_error(self, monkeypatch):
        monkeypatch.delenv("NOPE_NOT_SET", raising=False)

        with pytest.raises(McpConfigError) as excinfo:
            parse_mcp_config(
                {"servers": {"fs": _server(env={"TOKEN": "${NOPE_NOT_SET}"})}}
            )

        assert "NOPE_NOT_SET" in str(excinfo.value)
        assert "export NOPE_NOT_SET" in (excinfo.value.hint or "")

    def test_literal_values_are_untouched(self):
        config = parse_mcp_config({"servers": {"fs": _server(args=["plain", "$5"])}})

        assert config.servers[0].args == ("plain", "$5")


@pytest.mark.unit
class TestScoping:
    def _config(self):
        return parse_mcp_config(
            {
                "safe_tools": ["filesystem__read_file"],
                "servers": {
                    "filesystem": _server(
                        agents=["code"],
                        allowed_tools=["read_file", "list_directory"],
                        denied_tools=["list_directory"],
                    ),
                    "global": _server(),
                },
            }
        )

    def test_agent_scoping(self):
        config = self._config()

        assert [s.name for s in config.for_agent("code")] == ["filesystem", "global"]
        assert [s.name for s in config.for_agent("plan")] == ["global"]

    def test_tool_filters(self):
        server = self._config().servers[0]

        assert server.allows_tool("read_file")
        assert not server.allows_tool("list_directory")  # denied wins
        assert not server.allows_tool("write_file")  # not in allowed_tools

    def test_no_filters_means_everything(self):
        server = self._config().servers[1]

        assert server.allows_tool("anything")

    def test_disabled_config_exposes_no_servers(self):
        disabled = McpConfig(enabled=False)

        assert disabled.for_agent("code") == ()


@pytest.mark.unit
def test_env_guard_applies_to_every_config_source(monkeypatch, tmp_path):
    path = _write(tmp_path, {"servers": {"fs": _server()}})
    monkeypatch.setenv(DISABLE_ENV_VAR, "1")

    assert load_mcp_config(path).enabled is False
    assert os.environ[DISABLE_ENV_VAR] == "1"


@pytest.mark.unit
class TestErrorRendering:
    def test_config_error_is_labelled_mcp_json(self):
        block = McpConfigError("MCP config not found: mcp.json").render()

        assert "[MCP error] mcp.json — config" in block
        assert "MCP config not found: mcp.json" in block

    def test_server_error_names_the_server_and_kind(self):
        block = McpError(
            "handshake failed", server="fs", kind="connect", hint="check the command"
        ).render()

        assert "[MCP error] fs — connect" in block
        assert "hint:" in block and "check the command" in block

    def test_str_is_the_bare_message(self):
        error = McpError("boom", server="fs")

        assert str(error) == "boom"
        assert error.render() != str(error)
