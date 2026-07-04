import pytest
from pathlib import Path
from unittest.mock import Mock

from utils import session_manager


@pytest.mark.unit
class TestSessionManager:
    def test_load_system_prompt_defaults_to_constant(self):
        assert session_manager.load_system_prompt() == session_manager.SYSTEM_PROMPT

    def test_load_system_prompt_reads_existing_file(self, tmp_path):
        prompt_file = tmp_path / "system_prompt.txt"
        prompt_file.write_text("  Custom system prompt  \n", encoding="utf-8")

        result = session_manager.load_system_prompt(str(prompt_file))

        assert result == "Custom system prompt"

    def test_load_system_prompt_missing_file_returns_default(self, tmp_path, capsys):
        prompt_file = tmp_path / "missing_prompt.txt"

        result = session_manager.load_system_prompt(str(prompt_file))

        assert result == session_manager.SYSTEM_PROMPT
        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_load_system_prompt_read_error_returns_default(self, tmp_path, monkeypatch):
        prompt_file = tmp_path / "broken_prompt.txt"
        prompt_file.write_text("ignored", encoding="utf-8")

        def raise_io_error(self, encoding="utf-8"):
            raise IOError("unable to read")

        monkeypatch.setattr(Path, "read_text", raise_io_error)

        result = session_manager.load_system_prompt(str(prompt_file))

        assert result == session_manager.SYSTEM_PROMPT

    def test_init_session_db_creates_new_session_when_no_resume(
        self, monkeypatch, capsys
    ):
        mock_db = Mock()
        mock_db.create_session.return_value = 42
        monkeypatch.setattr(session_manager, "SessionDB", Mock(return_value=mock_db))

        session_id = session_manager.init_session_db(
            resume_session=None,
            session_name="New session",
            system_prompt="Hello",
            agent_name="test",
        )

        assert session_id == 42
        mock_db.create_session.assert_called_once_with(
            "New session", "Hello", agent_name="test"
        )
        assert "Created new session" in capsys.readouterr().out

    def test_init_session_db_resumes_existing_session(self, monkeypatch, capsys):
        mock_db = Mock()
        mock_db.get_session.return_value = {
            "name": "Existing session",
            "system_prompt": "Saved prompt",
        }
        monkeypatch.setattr(session_manager, "SessionDB", Mock(return_value=mock_db))

        session_id = session_manager.init_session_db(
            resume_session=7,
            session_name="Ignored",
            system_prompt="Ignored",
        )

        assert session_id == 7
        assert "Resuming session" in capsys.readouterr().out

    def test_init_session_db_falls_back_to_creation_when_resume_not_found(
        self, monkeypatch, capsys
    ):
        mock_db = Mock()
        mock_db.get_session.return_value = None
        mock_db.create_session.return_value = 99
        monkeypatch.setattr(session_manager, "SessionDB", Mock(return_value=mock_db))

        session_id = session_manager.init_session_db(
            resume_session=10,
            session_name="Fallback session",
            system_prompt="Fallback prompt",
        )

        assert session_id == 99
        assert "creating a new session" in capsys.readouterr().out
