"""MCP tool discovery, the shared registry, and BaseAgent integration."""

import types
from unittest.mock import Mock

import pytest

from core.base_agent import BaseAgent
from core.permissions import SAFE_TOOLS, AutoDenyResolver
from utils.mcp import manager as mcp_manager
from utils.mcp.config import McpConfig, McpServerConfig, parse_mcp_config
from utils.mcp.errors import McpError
from utils.mcp.manager import McpToolProvider, close_mcp_servers, connected_servers


def _mcp_tool(name):
    return types.SimpleNamespace(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )


class _FakeServer:
    """Stands in for utils.mcp.connection.McpServer (no SDK, no process)."""

    tools_by_server: dict[str, list] = {}
    failing: set[str] = set()
    instances: list = []
    capabilities: dict[str, dict] = {}
    resource_calls: list = []

    def __init__(self, config):
        self.config = config
        self.starts = 0
        self.closes = 0
        self.calls = []
        _FakeServer.instances.append(self)

    def start(self):
        if self.config.name in _FakeServer.failing:
            raise McpError(
                f"MCP server '{self.config.name}' failed to start.",
                server=self.config.name,
                kind="connect",
                hint="check the command",
            )
        self.starts += 1

    @property
    def tools(self):
        if not self.starts:
            self.start()
        return _FakeServer.tools_by_server.get(self.config.name, [])

    def call_tool(self, tool_name, arguments=None):
        self.calls.append((tool_name, arguments))
        return f"{self.config.name}:{tool_name}:{sorted((arguments or {}).items())}"

    def supports(self, capability):
        return bool(_FakeServer.capabilities.get(self.config.name, {}).get(capability))

    def list_resources(self):
        return f"{self.config.name}:list_resources"

    def read_resource(self, uri):
        _FakeServer.resource_calls.append((self.config.name, uri))
        return f"{self.config.name}:resource:{uri}"

    def list_prompts(self):
        return f"{self.config.name}:list_prompts"

    def get_prompt(self, name, arguments=None):
        return f"{self.config.name}:prompt:{name}:{sorted((arguments or {}).items())}"

    def close(self):
        self.closes += 1


@pytest.fixture(autouse=True)
def _fake_transport(monkeypatch):
    """Replace the connection layer and reset the registry per test."""
    close_mcp_servers()
    _FakeServer.instances = []
    _FakeServer.failing = set()
    _FakeServer.tools_by_server = {}
    _FakeServer.capabilities = {}
    _FakeServer.resource_calls = []
    monkeypatch.setattr(mcp_manager, "McpServer", _FakeServer)
    yield
    close_mcp_servers()


def _config(*servers, safe_tools=()):
    return McpConfig(enabled=True, safe_tools=tuple(safe_tools), servers=tuple(servers))


def _server(name="fs", **overrides):
    values = dict(name=name, command="fake")
    values.update(overrides)
    return McpServerConfig(**values)


@pytest.mark.unit
class TestProvider:
    def test_tools_are_namespaced_and_callable(self):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read_file")]}
        provider = McpToolProvider("code", _config(_server("fs")))

        assert [tool["function"]["name"] for tool in provider.tools] == [
            "mcp__fs__read_file"
        ]
        callable_ = provider.tool_map["mcp__fs__read_file"]
        assert callable_(q="hi") == "fs:read_file:[('q', 'hi')]"

    def test_agent_scoping_filters_servers(self):
        _FakeServer.tools_by_server = {
            "fs": [_mcp_tool("read")],
            "gh": [_mcp_tool("issue")],
        }
        config = _config(_server("fs", agents=("code",)), _server("gh"))

        code = McpToolProvider("code", config)
        plan = McpToolProvider("plan", config)

        assert {t["function"]["name"] for t in code.tools} == {
            "mcp__fs__read",
            "mcp__gh__issue",
        }
        assert {t["function"]["name"] for t in plan.tools} == {"mcp__gh__issue"}

    def test_tool_filters_are_applied(self):
        _FakeServer.tools_by_server = {
            "fs": [_mcp_tool("read"), _mcp_tool("write"), _mcp_tool("delete")]
        }
        provider = McpToolProvider(
            "code",
            _config(
                _server(
                    "fs",
                    allowed_tools=("read", "write", "delete"),
                    denied_tools=("delete",),
                )
            ),
        )

        assert {t["function"]["name"] for t in provider.tools} == {
            "mcp__fs__read",
            "mcp__fs__write",
        }

    def test_optional_failure_is_a_warning_and_other_servers_still_load(self):
        _FakeServer.failing = {"broken"}
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read")]}

        provider = McpToolProvider("code", _config(_server("broken"), _server("fs")))

        assert [t["function"]["name"] for t in provider.tools] == ["mcp__fs__read"]
        assert len(provider.warnings) == 1
        assert provider.warnings[0].server == "broken"
        # Startup output surfaces the problem instead of hiding it.
        assert any("MCP error" in line for line in provider.report_lines())

    def test_required_failure_aborts_construction(self):
        _FakeServer.failing = {"broken"}

        with pytest.raises(McpError):
            McpToolProvider("code", _config(_server("broken", required=True)))

    def test_disabled_config_exposes_nothing(self):
        provider = McpToolProvider("code", McpConfig(enabled=False))

        assert provider.tools == []
        assert provider.tool_map == {}
        assert provider.report_lines() == []

    def test_report_line_lists_servers_and_counts(self):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("a"), _mcp_tool("b")]}
        provider = McpToolProvider("code", _config(_server("fs")))

        assert provider.report_lines() == ["[MCP] 2 tools from fs (2 tools)"]


@pytest.mark.unit
class TestSafeTools:
    def test_exact_and_bare_names_are_accepted(self):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read"), _mcp_tool("write")]}
        provider = McpToolProvider(
            "code",
            _config(_server("fs"), safe_tools=["mcp__fs__read", "fs__write"]),
        )

        assert provider.safe_tool_names == {"mcp__fs__read", "mcp__fs__write"}

    def test_wildcards(self):
        _FakeServer.tools_by_server = {
            "fs": [_mcp_tool("read")],
            "gh": [_mcp_tool("x")],
        }
        provider = McpToolProvider(
            "code", _config(_server("fs"), _server("gh"), safe_tools=["mcp__fs__*"])
        )

        assert provider.safe_tool_names == {"mcp__fs__read"}

    def test_unknown_safe_name_is_harmless(self):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read")]}
        provider = McpToolProvider(
            "code", _config(_server("fs"), safe_tools=["mcp__nope__x"])
        )

        assert provider.safe_tool_names == set()


@pytest.mark.unit
class TestRegistry:
    def test_connections_are_reused_across_agents(self):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read")]}
        config = _config(_server("fs"))

        first = McpToolProvider("code", config)
        second = McpToolProvider("plan", config)

        assert first.tool_map and second.tool_map
        assert len(_FakeServer.instances) == 1
        assert _FakeServer.instances[0].starts == 1
        assert list(connected_servers()) == ["fs"]

    def test_close_is_idempotent_and_clears_the_registry(self):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read")]}
        McpToolProvider("code", _config(_server("fs")))

        close_mcp_servers()
        close_mcp_servers()

        assert _FakeServer.instances[0].closes == 1
        assert connected_servers() == {}


@pytest.mark.unit
class TestBaseAgentIntegration:
    def _agent(self, monkeypatch, config, resolver=None):
        class _TestAgent(BaseAgent):
            def get_tools(self):
                return [{"type": "function", "function": {"name": "local_tool"}}]

            def get_tool_map(self):
                return {"local_tool": lambda **kwargs: "local"}

            def get_system_prompt(self):
                return "sys"

        monkeypatch.setattr("core.base_agent.SessionDB", Mock)
        return _TestAgent(
            model="local",
            llm_base="http://localhost",
            mcp_config=config,
            permission_resolver=resolver,
        )

    def test_agent_tools_and_map_are_extended(self, monkeypatch):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read_file")]}

        agent = self._agent(monkeypatch, _config(_server("fs")))

        assert {t["function"]["name"] for t in agent.tools} == {
            "local_tool",
            "mcp__fs__read_file",
        }
        assert "mcp__fs__read_file" in agent.tool_map
        assert agent.tool_map["local_tool"]() == "local"
        # The shared module-level dicts used by real agents are not mutated.
        assert agent.tool_map is not agent.get_tool_map()

    def test_mcp_tools_are_gated_by_default(self, monkeypatch):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read_file")]}
        resolver = AutoDenyResolver()

        agent = self._agent(monkeypatch, _config(_server("fs")), resolver=resolver)
        outcome = agent.permission_policy.check("mcp__fs__read_file", {})

        # Fail-closed: not in SAFE_TOOLS, so it asks (denied here).
        assert "mcp__fs__read_file" not in SAFE_TOOLS
        assert outcome.allowed is False

    def test_safe_tools_config_skips_the_prompt(self, monkeypatch):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read_file")]}

        agent = self._agent(
            monkeypatch,
            _config(_server("fs"), safe_tools=["mcp__fs__read_file"]),
            resolver=AutoDenyResolver(),
        )
        outcome = agent.permission_policy.check("mcp__fs__read_file", {})

        assert outcome.allowed is True

    def test_shutdown_closes_mcp_connections(self, monkeypatch):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read_file")]}
        agent = self._agent(monkeypatch, _config(_server("fs")))

        agent.shutdown()
        agent.shutdown()

        assert _FakeServer.instances[0].closes == 1
        assert connected_servers() == {}


@pytest.mark.unit
def test_config_from_json_drives_the_provider(monkeypatch):
    _FakeServer.tools_by_server = {"filesystem": [_mcp_tool("read_file")]}
    data = {
        "servers": {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "server"],
                "agents": ["code"],
            }
        }
    }
    monkeypatch.delenv("BASIC_AGENTS_NO_MCP", raising=False)

    provider = McpToolProvider("code", parse_mcp_config(data))

    assert provider.tools[0]["function"]["name"] == "mcp__filesystem__read_file"


@pytest.mark.unit
class TestResourceAndPromptTools:
    def test_not_exposed_unless_configured(self):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read")]}
        _FakeServer.capabilities = {"fs": {"resources": True, "prompts": True}}

        provider = McpToolProvider("code", _config(_server("fs")))

        assert {t["function"]["name"] for t in provider.tools} == {"mcp__fs__read"}

    def test_exposed_when_configured_and_supported(self):
        _FakeServer.tools_by_server = {"fs": []}
        _FakeServer.capabilities = {"fs": {"resources": True, "prompts": True}}

        provider = McpToolProvider(
            "code",
            _config(_server("fs", expose_resources=True, expose_prompts=True)),
        )

        names = {t["function"]["name"] for t in provider.tools}
        assert names == {
            "mcp__fs__list_resources",
            "mcp__fs__read_resource",
            "mcp__fs__list_prompts",
            "mcp__fs__get_prompt",
        }
        read_schema = next(
            t["function"]["parameters"]
            for t in provider.tools
            if t["function"]["name"] == "mcp__fs__read_resource"
        )
        assert read_schema["required"] == ["uri"]

    def test_skipped_when_the_server_lacks_the_capability(self):
        _FakeServer.tools_by_server = {"fs": []}
        _FakeServer.capabilities = {"fs": {"resources": False, "prompts": False}}

        provider = McpToolProvider(
            "code",
            _config(_server("fs", expose_resources=True, expose_prompts=True)),
        )

        assert provider.tools == []

    def test_synthetic_tools_dispatch_to_the_server(self):
        _FakeServer.tools_by_server = {"fs": []}
        _FakeServer.capabilities = {"fs": {"resources": True, "prompts": True}}
        provider = McpToolProvider(
            "code",
            _config(_server("fs", expose_resources=True, expose_prompts=True)),
        )

        provider.tool_map["mcp__fs__read_resource"](uri="file:///a.txt")
        prompt = provider.tool_map["mcp__fs__get_prompt"](
            name="greet", arguments={"who": "Ada"}
        )

        assert _FakeServer.resource_calls == [("fs", "file:///a.txt")]
        assert prompt == "fs:prompt:greet:[('who', 'Ada')]"

    def test_real_tool_wins_over_a_synthetic_name(self):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read_resource")]}
        _FakeServer.capabilities = {"fs": {"resources": True, "prompts": False}}

        provider = McpToolProvider(
            "code", _config(_server("fs", expose_resources=True))
        )

        # The colliding synthetic is skipped; the other one is still added.
        assert {t["function"]["name"] for t in provider.tools} == {
            "mcp__fs__read_resource",
            "mcp__fs__list_resources",
        }
        # The real tool wins: dispatch goes to call_tool, not read_resource.
        assert (
            provider.tool_map["mcp__fs__read_resource"](uri="x")
            == "fs:read_resource:[('uri', 'x')]"
        )
        assert any("already exposes" in warning.message for warning in provider.warnings)

    def test_synthetic_tools_can_be_pre_approved(self):
        _FakeServer.tools_by_server = {"fs": []}
        _FakeServer.capabilities = {"fs": {"resources": True, "prompts": False}}

        provider = McpToolProvider(
            "code",
            _config(
                _server("fs", expose_resources=True),
                safe_tools=["mcp__fs__read_resource"],
            ),
        )

        assert provider.safe_tool_names == {"mcp__fs__read_resource"}


@pytest.mark.unit
class TestStatusHelpers:
    def test_describe_servers_reports_tools_and_capabilities(self):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("a"), _mcp_tool("b")]}
        _FakeServer.capabilities = {"fs": {"resources": True, "prompts": False}}

        rows = mcp_manager.describe_servers(
            _config(_server("fs", agents=("code",), url="", transport="stdio"))
        )

        assert rows == [
            {
                "name": "fs",
                "transport": "stdio",
                "target": "fake",
                "agents": "code",
                "tools": 2,
                "resources": True,
                "prompts": False,
                "error": None,
            }
        ]

    def test_describe_servers_reports_failures_instead_of_raising(self):
        _FakeServer.failing = {"broken"}

        rows = mcp_manager.describe_servers(_config(_server("broken")))

        assert rows[0]["error"] is not None
        assert rows[0]["tools"] == 0

    def test_close_single_server(self):
        _FakeServer.tools_by_server = {"fs": [_mcp_tool("read")]}
        McpToolProvider("code", _config(_server("fs")))

        assert mcp_manager.close_mcp_server("fs") is True
        assert mcp_manager.close_mcp_server("fs") is False
        assert _FakeServer.instances[0].closes == 1
        assert connected_servers() == {}
