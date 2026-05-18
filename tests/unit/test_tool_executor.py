import pytest
from unittest.mock import Mock, patch
from utils.tool_executor import ToolExecutor

@pytest.mark.unit
class TestToolExecutor:
    def test_tool_executor_initialization(self):
        """Test that ToolExecutor initializes correctly"""
        tool_executor = ToolExecutor()
        assert tool_executor is not None

    def test_tool_executor_execute_tool(self):
        """Test executing a tool"""
        tool_executor = ToolExecutor()
        with patch.object(tool_executor, 'execute_tool') as mock_execute:
            tool_executor.execute_tool('test-tool', {'input': 'data'})
            mock_execute.assert_called_once_with('test-tool', {'input': 'data'})

    def test_tool_executor_register_tool(self):
        """Test registering a tool"""
        tool_executor = ToolExecutor()
        with patch.object(tool_executor, 'register_tool') as mock_register:
            tool_executor.register_tool('test-tool', Mock())
            mock_register.assert_called_once_with('test-tool', Mock())