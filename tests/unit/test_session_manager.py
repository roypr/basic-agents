import pytest
from unittest.mock import Mock, patch
from utils.session_manager import SessionManager

@pytest.mark.unit
class TestSessionManager:
    def test_session_manager_initialization(self):
        """Test that SessionManager initializes correctly"""
        session_manager = SessionManager()
        assert session_manager is not None

    def test_session_manager_create_session(self):
        """Test creating a new session"""
        session_manager = SessionManager()
        with patch('utils.session_manager.uuid') as mock_uuid:
            mock_uuid.uuid4.return_value = 'test-uuid'
            session_id = session_manager.create_session()
            assert session_id == 'test-uuid'

    def test_session_manager_get_session(self):
        """Test getting an existing session"""
        session_manager = SessionManager()
        session_id = 'test-session-id'
        session = session_manager.get_session(session_id)
        assert session is not None