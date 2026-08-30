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
