import pytest
import os
import tempfile
from unittest.mock import Mock, MagicMock
from dotenv import load_dotenv

# Load environment variables for testing
load_dotenv()

@pytest.fixture
def mock_db():
    """Mock database fixture for testing"""
    return Mock()

@pytest.fixture
def mock_llm_client():
    """Mock LLM client fixture for testing"""
    return Mock()

@pytest.fixture
def temp_dir():
    """Create temporary directory for tests"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # Cleanup would happen here if needed