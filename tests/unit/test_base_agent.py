import json
import pytest

from core.base_agent import BaseAgent


@pytest.mark.unit
class TestToolContent:
    def _agent(self) -> BaseAgent:
        """Instantiate without running __init__ side effects (DB, adapters)."""
        return BaseAgent.__new__(BaseAgent)

    def test_plain_text_passes_through(self):
        agent = self._agent()
        assert agent._tool_content("plain result") == "plain result"

    def test_non_image_json_passes_through(self):
        agent = self._agent()
        payload = json.dumps({"type": "text", "text": "hello"})
        assert agent._tool_content(payload) == payload

    def test_image_json_becomes_multimodal_content(self):
        agent = self._agent()
        payload = json.dumps(
            {
                "type": "image",
                "mime": "image/png",
                "data": "QUJD",
                "note": "Image loaded.",
            }
        )
        content = agent._tool_content(payload)
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "Image loaded."}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


@pytest.mark.unit
class TestDeserializeToolContent:
    def _agent(self) -> BaseAgent:
        return BaseAgent.__new__(BaseAgent)

    def test_multimodal_json_is_rehydrated(self):
        agent = self._agent()
        stored = json.dumps(
            [
                {"type": "text", "text": "Image loaded."},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,QUJD"},
                },
            ]
        )
        content = agent._deserialize_tool_content(stored)
        assert isinstance(content, list)
        assert content[1]["type"] == "image_url"

    def test_plain_json_array_stays_text(self):
        agent = self._agent()
        stored = json.dumps(["config.py", "main.py"])
        assert agent._deserialize_tool_content(stored) == stored

    def test_empty_array_stays_text(self):
        agent = self._agent()
        assert agent._deserialize_tool_content("[]") == "[]"

    def test_non_json_stays_text(self):
        agent = self._agent()
        assert agent._deserialize_tool_content("plain result") == "plain result"
