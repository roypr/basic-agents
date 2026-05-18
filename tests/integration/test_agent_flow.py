import pytest
from unittest.mock import patch
from agents.code.agent import CodeAgent
from db.session_db import SessionDB

@pytest.mark.integration
class TestAgentFlow:
    def test_agent_end_to_end_flow(self, tmp_path):
        """Test end-to-end agent flow"""
        original_init = SessionDB.__init__

        def patched_init(self, db_path: str = "db/sessions.db", *args, **kwargs):
            return original_init(self, str(tmp_path / "sessions.db"), *args, **kwargs)

        with patch("db.session_db.SessionDB.__init__", new=patched_init), \
             patch("core.base_agent.call_llm_streaming") as mock_llm:
            mock_llm.return_value = {
                "role": "assistant",
                "content": "This is a test answer.",
            }

            agent = CodeAgent(
                model="local",
                llm_base="http://localhost:8080",
                max_turns=1,
                session_name="Integration Test Session",
            )
            agent.run("What is the status?")

            sessions = agent.session_db.get_sessions()
            assert len(sessions) == 1

            session_id = sessions[0]["id"]
            messages = agent.session_db.get_messages(session_id)

            assert [message["role"] for message in messages] == ["user", "assistant"]
            assert any(
                "test answer" in message["content"].lower()
                for message in messages
                if message["role"] == "assistant"
            )