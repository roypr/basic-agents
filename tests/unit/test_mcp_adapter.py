"""MCP -> tool-schema and CallToolResult -> tool-content translation."""

import json
from types import SimpleNamespace

import pytest

from core.base_agent import BaseAgent
from utils.mcp.adapter import (
    MAX_TOOL_NAME,
    public_tool_name,
    render_tool_result,
    to_openai_tool,
)


def _tool(name="read_file", description="Read a file", schema=None, **kwargs):
    fields = {"name": name, "description": description, "input_schema": schema}
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _image(data="QUJD", mime="image/png", **kwargs):
    block = {"type": "image", "data": data, "mime_type": mime}
    block.update(kwargs)
    return SimpleNamespace(**block)


def _result(content=(), is_error=False, structured=None):
    return SimpleNamespace(
        content=list(content), is_error=is_error, structured_content=structured
    )


@pytest.mark.unit
class TestNaming:
    def test_namespaced_with_server(self):
        assert public_tool_name("github", "create_issue") == "mcp__github__create_issue"

    def test_unsafe_characters_are_sanitized(self):
        name = public_tool_name("my server", "do:thing/now")

        assert name == "mcp__my_server__do_thing_now"

    def test_long_names_are_unique_and_provider_safe(self):
        long_a = "a" * 80
        long_b = "a" * 79 + "b"

        first = public_tool_name("server", long_a)
        second = public_tool_name("server", long_b)

        assert len(first) <= MAX_TOOL_NAME
        assert len(second) <= MAX_TOOL_NAME
        assert first != second  # hash suffix keeps them distinct


@pytest.mark.unit
class TestSchema:
    def test_v2_snake_case_schema(self):
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        tool = to_openai_tool("mcp__fs__read", _tool(schema=schema), server="fs")

        assert tool["type"] == "function"
        assert tool["function"]["name"] == "mcp__fs__read"
        assert tool["function"]["parameters"] == schema
        assert tool["function"]["description"].startswith("[fs] ")

    def test_v1_camel_case_schema(self):
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        tool = _tool()
        tool.inputSchema = schema

        assert (
            to_openai_tool("mcp__s__t", tool, server="s")["function"]["parameters"]
            == schema
        )

    def test_missing_schema_becomes_empty_object_schema(self):
        tool = to_openai_tool("mcp__s__t", _tool(schema=None), server="s")

        assert tool["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_description_is_bounded(self):
        tool = to_openai_tool("mcp__s__t", _tool(description="x" * 5000), server="s")

        assert len(tool["function"]["description"]) <= 1024

    def test_empty_description_still_identifies_the_server(self):
        tool = to_openai_tool("mcp__s__t", _tool(description=""), server="s")

        assert "s" in tool["function"]["description"]


@pytest.mark.unit
class TestResultRendering:
    def test_text_blocks_are_joined(self):
        assert render_tool_result(_result([_text("one"), _text("two")])) == "one\ntwo"

    def test_error_result_is_prefixed(self):
        rendered = render_tool_result(_result([_text("boom")], is_error=True))

        assert rendered == "Error: boom"

    def test_error_without_text_still_reads_as_an_error(self):
        assert render_tool_result(_result([], is_error=True)).startswith("Error:")

    def test_empty_result_is_explicit(self):
        assert render_tool_result(_result([])) == "(no content returned)"

    def test_structured_content_is_used_when_no_text(self):
        rendered = render_tool_result(
            _result([], structured={"files": ["a.py"], "count": 1})
        )

        assert json.loads(rendered) == {"files": ["a.py"], "count": 1}

    def test_resource_text_and_binary_note(self):
        text_resource = SimpleNamespace(
            type="resource",
            resource=SimpleNamespace(uri="file:///a.txt", text="hello"),
        )
        binary_resource = SimpleNamespace(
            type="resource",
            resource=SimpleNamespace(uri="file:///b.bin", mime_type="application/pdf"),
        )

        rendered = render_tool_result(_result([text_resource, binary_resource]))

        assert "hello" in rendered
        assert "binary resource: file:///b.bin [application/pdf]" in rendered

    def test_image_becomes_the_multimodal_marker(self):
        rendered = render_tool_result(
            _result([_text("chart"), _image()]), tool_name="plot"
        )
        payload = json.loads(rendered)

        assert payload["type"] == "image"
        assert payload["mime"] == "image/png"
        assert payload["data"] == "QUJD"
        assert "chart" in payload["note"]
        assert "plot" in payload["note"]

    def test_image_marker_is_understood_by_the_agent_loop(self):
        """The marker must produce a native image part for every provider."""
        rendered = render_tool_result(_result([_image()]), tool_name="plot")

        content = BaseAgent.__new__(BaseAgent)._tool_content(rendered)

        assert isinstance(content, list)
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "data:image/png;base64,QUJD"

    def test_v1_camel_case_fields_are_tolerated(self):
        result = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="legacy")],
            isError=True,
        )

        assert render_tool_result(result) == "Error: legacy"
