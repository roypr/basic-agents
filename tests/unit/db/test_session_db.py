import pytest
from db.session_db import SessionDB

@pytest.mark.unit
class TestSessionDB:
    def test_session_db_initialization(self, tmp_path):
        """Test that SessionDB initializes and creates the DB file."""
        db_path = tmp_path / "sessions.db"
        session_db = SessionDB(str(db_path))

        assert session_db is not None
        assert db_path.exists()
        assert db_path.stat().st_size > 0

    def test_session_db_create_and_get_session(self, tmp_path):
        """Test creating a session and retrieving it."""
        session_db = SessionDB(str(tmp_path / "sessions.db"))
        session_id = session_db.create_session("test-session", "test prompt")

        assert session_id > 0

        session = session_db.get_session(session_id)
        assert session is not None
        assert session["id"] == session_id
        assert session["name"] == "test-session"
        assert session["system_prompt"] == "test prompt"
        assert session["is_active"] == 1

    def test_session_db_add_and_get_messages(self, tmp_path):
        """Test adding messages and reading them back."""
        session_db = SessionDB(str(tmp_path / "sessions.db"))
        session_id = session_db.create_session("test-session")

        session_db.add_message(session_id, "user", "hello")
        session_db.add_message(session_id, "assistant", "hi", tool_calls=[{"tool": "search"}])

        messages = session_db.get_messages(session_id)
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "hello"}
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "hi"
        assert messages[1]["tool_calls"] == [{"tool": "search"}]

    def test_session_db_tool_message_tool_call_id(self, tmp_path):
        """Test tool message persistence with tool_call_id."""
        session_db = SessionDB(str(tmp_path / "sessions.db"))
        session_id = session_db.create_session("test-session")

        session_db.add_message(session_id, "tool", "tool output", tool_call_id="abc123")

        messages = session_db.get_messages(session_id)
        assert len(messages) == 1
        assert messages[0] == {
            "role": "tool",
            "content": "tool output",
            "tool_call_id": "abc123",
        }

    def test_session_db_delete_session(self, tmp_path):
        """Test soft-deleting a session."""
        session_db = SessionDB(str(tmp_path / "sessions.db"))
        session_id = session_db.create_session("test-session")

        assert session_db.delete_session(session_id) is True
        assert session_db.get_session(session_id) is None
