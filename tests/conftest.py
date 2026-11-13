import os

import pytest
import tempfile
from unittest.mock import Mock
from dotenv import load_dotenv

# Load environment variables for testing
load_dotenv()


@pytest.fixture(autouse=True)
def _disable_mcp():
    """Keep the suite hermetic: never spawn real MCP servers.

    A developer with a local ``mcp.json`` would otherwise launch ``npx`` (or
    worse) in every agent-construction test. Individual tests opt back in by
    passing an explicit ``McpConfig`` and monkeypatching the connection layer.
    """
    previous = os.environ.get("BASIC_AGENTS_NO_MCP")
    os.environ["BASIC_AGENTS_NO_MCP"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("BASIC_AGENTS_NO_MCP", None)
        else:
            os.environ["BASIC_AGENTS_NO_MCP"] = previous


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


@pytest.fixture
def auto_allow_resolver():
    """Non-interactive resolver that always allows once.

    Default for BaseAgent, so the existing suite runs untouched.
    """
    from core.permissions import AutoAllowResolver

    return AutoAllowResolver()


@pytest.fixture
def auto_deny_resolver():
    """Non-interactive resolver that always denies once (fail-closed)."""
    from core.permissions import AutoDenyResolver

    return AutoDenyResolver()
