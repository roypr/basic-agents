"""Translate between MCP and this framework's tool dialect.

MCP tools are already JSON-Schema functions, so the mapping is thin: rename
into a namespaced OpenAI function name, and turn ``CallToolResult`` content
blocks into the string/multimodal payload the agent loop already understands.

Image results reuse the existing ``{"type": "image", ...}`` JSON marker, which
:meth:`core.base_agent.BaseAgent._tool_content` converts into a native image
content part for every provider adapter.
"""

from __future__ import annotations

import hashlib
import json
import re

# OpenAI-compatible providers cap function names at 64 characters.
MAX_TOOL_NAME = 64
MAX_DESCRIPTION = 1024

_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
PUBLIC_PREFIX = "mcp__"


def _sanitize(text: str) -> str:
    cleaned = _NAME_CHARS.sub("_", (text or "").strip())
    return cleaned or "unnamed"


def public_tool_name(server: str, tool: str) -> str:
    """Namespaced, provider-safe function name: ``mcp__<server>__<tool>``.

    Namespacing keeps two servers from colliding on a generic tool name (and
    makes per-server permission rules readable).  Over-long names keep a hash
    suffix so distinct tools never collapse onto the same name.
    """
    raw = f"{PUBLIC_PREFIX}{_sanitize(server)}__{_sanitize(tool)}"
    if len(raw) <= MAX_TOOL_NAME:
        return raw
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{raw[: MAX_TOOL_NAME - len(digest) - 1]}_{digest}"


def _field(obj, *names, default=None):
    """Read the first present attribute, tolerating camelCase/snake_case."""
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def tool_schema(mcp_tool) -> dict:
    """JSON Schema for an MCP tool, always object-shaped."""
    schema = _field(mcp_tool, "input_schema", "inputSchema", default=None)
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}
    return schema


def tool_description(mcp_tool, *, server: str) -> str:
    description = _field(mcp_tool, "description", default="") or ""
    description = " ".join(str(description).split())
    prefix = f"[{server}] " if server else ""
    text = f"{prefix}{description}" if description else f"{prefix}MCP tool"
    if len(text) > MAX_DESCRIPTION:
        text = text[: MAX_DESCRIPTION - 1].rstrip() + "…"
    return text


def to_openai_tool(public_name: str, mcp_tool, *, server: str) -> dict:
    """Build the ``{"type": "function", ...}`` dict every adapter accepts."""
    return {
        "type": "function",
        "function": {
            "name": public_name,
            "description": tool_description(mcp_tool, server=server),
            "parameters": tool_schema(mcp_tool),
        },
    }


def _block_type(block) -> str:
    return str(_field(block, "type", default="") or "")


def _text_of(block) -> str:
    text = _field(block, "text", default="")
    return text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)


def _resource_text(block) -> str:
    resource = _field(block, "resource", default=None)
    if resource is None:
        return ""
    text = _field(resource, "text", default=None)
    if isinstance(text, str) and text:
        return text
    uri = _field(resource, "uri", default="")
    mime = _field(resource, "mime_type", "mimeType", default="") or "unknown"
    return f"(binary resource: {uri} [{mime}])"


def render_tool_result(result, *, tool_name: str = "") -> str:
    """Flatten a ``CallToolResult`` into tool-result content.

    Returns a string so the executor and session DB stay unchanged; a single
    image block is returned as the image marker JSON so the model can see it.
    """
    blocks = _field(result, "content", default=None) or []
    texts: list[str] = []
    image: dict | None = None

    for block in blocks:
        block_type = _block_type(block)
        if block_type == "text":
            text = _text_of(block)
            if text:
                texts.append(text)
        elif block_type == "image":
            data = _field(block, "data", default="") or ""
            mime = _field(block, "mime_type", "mimeType", default="") or "image/png"
            if image is None and data:
                image = {
                    "type": "image",
                    "mime": mime,
                    "data": data,
                    "path": f"mcp:{tool_name}" if tool_name else "mcp",
                    "note": (
                        f"Image returned by MCP tool '{tool_name}' ({mime}). "
                        "You can see and analyze it."
                        if tool_name
                        else f"Image returned by an MCP tool ({mime})."
                    ),
                }
            elif data:
                texts.append(f"(additional {mime} image omitted)")
        elif block_type == "resource":
            resource_text = _resource_text(block)
            if resource_text:
                texts.append(resource_text)
        elif block_type:
            texts.append(_text_of(block))
    if image is not None:
        # The note is the only text the model sees alongside the image.
        note = "\n".join(text for text in texts if text).strip()
        if note:
            image["note"] = f"{note}\n\n{image['note']}"
        return json.dumps(image, ensure_ascii=False)

    structured = _field(result, "structured_content", "structuredContent", default=None)
    if not any(texts) and structured is not None:
        texts.append(json.dumps(structured, ensure_ascii=False))

    is_error = bool(_field(result, "is_error", "isError", default=False))
    text = "\n".join(t for t in texts if t).strip()
    if is_error:
        return f"Error: {text}" if text else "Error: MCP tool reported a failure"
    return text or "(no content returned)"


def _content_summary(block) -> str:
    """Render one prompt/resource content block as text."""
    block_type = _block_type(block)
    if block_type in ("text", ""):
        return _text_of(block)
    if block_type == "image":
        mime = _field(block, "mime_type", "mimeType", default="") or "image"
        return f"(image omitted: {mime})"
    if block_type == "resource":
        return _resource_text(block)
    return _text_of(block)


def render_resource_result(result) -> str:
    """Render ``ReadResourceResult`` (``contents`` blocks) as text."""
    contents = _field(result, "contents", default=None) or []
    parts: list[str] = []
    for item in contents:
        uri = _field(item, "uri", default="")
        mime = _field(item, "mime_type", "mimeType", default="") or "unknown"
        text = _field(item, "text", default=None)
        if isinstance(text, str) and text:
            parts.append(f"[{uri}]\n{text}" if uri else text)
            continue
        blob = _field(item, "blob", default=None)
        if blob is not None:
            size = len(blob) if isinstance(blob, (str, bytes)) else 0
            parts.append(f"(binary resource {uri} [{mime}], {size} bytes)")
            continue
        parts.append(f"(empty resource {uri} [{mime}])")
    return "\n\n".join(parts) or "(no content returned)"


def render_resource_list(result) -> str:
    """Render ``ListResourcesResult`` as a newline-per-resource listing."""
    resources = _field(result, "resources", default=None) or []
    lines = []
    for resource in resources:
        uri = _field(resource, "uri", default="")
        name = _field(resource, "name", default="") or ""
        mime = _field(resource, "mime_type", "mimeType", default="") or ""
        label = f"{uri} — {name}" if name and name != uri else uri
        lines.append(f"{label} [{mime}]" if mime else label)
    return "\n".join(lines) or "(no resources)"


def render_prompt_result(result) -> str:
    """Render ``GetPromptResult`` messages as a role-tagged transcript."""
    messages = _field(result, "messages", default=None) or []
    parts: list[str] = []
    for message in messages:
        role = _field(message, "role", default="user") or "user"
        content = _field(message, "content", default="")
        parts.append(f"{role}: {_content_summary(content)}")
    description = _field(result, "description", default="") or ""
    body = "\n\n".join(parts) or "(empty prompt)"
    return f"{description}\n\n{body}" if description else body


def render_prompt_list(result) -> str:
    """Render ``ListPromptsResult`` as a newline-per-prompt listing."""
    prompts = _field(result, "prompts", default=None) or []
    lines = []
    for prompt in prompts:
        name = _field(prompt, "name", default="") or ""
        description = _field(prompt, "description", default="") or ""
        arguments = _field(prompt, "arguments", default=None) or []
        names = [
            _field(argument, "name", default="")
            for argument in arguments
            if _field(argument, "name", default="")
        ]
        line = f"{name}({', '.join(names)})" if names else name
        lines.append(f"{line} — {description}" if description else line)
    return "\n".join(lines) or "(no prompts)"
