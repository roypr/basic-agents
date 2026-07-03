import pytest
from unittest.mock import patch
from agents.code.agent import CodeAgent
from db.session_db import SessionDB
from utils.history_compressor import compress_session


@pytest.mark.integration
class TestAgentFlow:
    def test_agent_end_to_end_flow(self, tmp_path):
        """Test end-to-end agent flow"""
        original_init = SessionDB.__init__

        def patched_init(self, db_path: str = "db/sessions.db", *args, **kwargs):
            return original_init(self, str(tmp_path / "sessions.db"), *args, **kwargs)

        with (
            patch("db.session_db.SessionDB.__init__", new=patched_init),
            patch("core.base_agent.call_llm_streaming") as mock_llm,
        ):
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


@pytest.mark.integration
class TestCompressFlow:
    def test_compress_session_with_tool_calls(self, tmp_path):
        """Test that compress_session strips tool artifacts and creates clean session."""
        db_path = tmp_path / "test_compress.db"
        db = SessionDB(str(db_path))

        # Create a session with system prompt
        session_id = db.create_session("Test Session", "You are a helpful assistant.")

        # Add messages simulating a turn with tool calls
        db.add_message(session_id, "system", "You are a helpful assistant.")
        db.add_message(session_id, "user", "Search for something.")
        db.add_message(
            session_id,
            "assistant",
            "Let me search...",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"q": "test"}'},
                }
            ],
        )
        db.add_message(
            session_id, "tool", '{"results": ["item1"]}', tool_call_id="call_1"
        )
        db.add_message(session_id, "assistant", "Here are the results.")
        db.add_message(session_id, "tool", "finished", tool_call_id="call_finish")

        # Compress the session
        new_id = compress_session(db, session_id, output_dir=str(tmp_path / "logs"))

        assert new_id is not None, "compress_session should return a new session ID"
        assert new_id != session_id, "New session ID should differ from original"

        # Verify original session is intact
        original_msgs = db.get_messages(session_id)
        assert len(original_msgs) == 6, "Original session should be unchanged"

        # Verify compressed session has no tool artifacts
        compressed_msgs = db.get_messages(new_id)
        for msg in compressed_msgs:
            assert msg["role"] != "tool", f"Tool messages should be removed: {msg}"
            assert "tool_calls" not in msg or msg["tool_calls"] is None, (
                f"tool_calls should be stripped from assistant messages: {msg}"
            )

        # Verify correct messages remain
        roles = [m["role"] for m in compressed_msgs]
        assert roles == ["system", "user", "assistant", "assistant"], (
            f"Expected [system, user, assistant, assistant], got {roles}"
        )

        # Verify raw export file exists
        export_file = tmp_path / "logs" / f"session_{session_id}_raw.json"
        assert export_file.exists(), "Raw export file should exist"

    def test_compress_nonexistent_session(self, tmp_path):
        """compress_session returns None for missing session."""
        db = SessionDB(str(tmp_path / "nonexistent.db"))
        result = compress_session(db, 999, output_dir=str(tmp_path / "logs"))
        assert result is None
