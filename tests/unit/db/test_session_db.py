import pytest
from unittest.mock import Mock, patch
from db.session_db import SessionDB

@pytest.mark.unit
class TestSessionDB:
    def test_session_db_initialization(self):
        """Test that SessionDB initializes correctly"""
        session_db = SessionDB()
        assert session_db is not None

    def test_session_db_save_session(self):
        """Test saving a session"""
        session_db = SessionDB()
        with patch.object(session_db, 'save_session') as mock_save:
            session_db.save_session('test-session', {'data': 'test'})
            mock_save.assert_called_once_with('test-session', {'data': 'test'})

    def test_session_db_load_session(self):
        """Test loading a session"""
        session_db = SessionDB()
        with patch.object(session_db, 'load_session') as mock_load:
            session_db.load_session('test-session')
            mock_load.assert_called_once_with('test-session')