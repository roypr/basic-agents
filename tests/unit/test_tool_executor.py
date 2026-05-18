import pytest
from unittest.mock import Mock
from utils.tool_executor import ToolExecutor, execute_tool_calls

@pytest.mark.unit
class TestToolExecutor:
    def test_tool_executor_initialization(self):
        """Test that ToolExecutor initializes correctly"""
        tool_executor = ToolExecutor()
        assert tool_executor is not None

    def test_tool_executor_execute_tool(self):
        """Test executing a tool with actual implementation"""
        tool_executor = ToolExecutor()

        # Mock a tool function
        def mock_tool_function(input):
            return f"Processed {input}"

        tool_executor.register_tool('test-tool', mock_tool_function)
        result = tool_executor.execute_tool('test-tool', {'input': 'data'})

        assert result == "Processed data", "Tool execution result is incorrect"

    def test_tool_executor_register_tool(self):
        """Test registering a tool and verifying its existence"""
        tool_executor = ToolExecutor()

        # Mock a tool function
        mock_tool_function = Mock()
        tool_executor.register_tool('test-tool', mock_tool_function)

        assert 'test-tool' in tool_executor.tools, "Tool was not registered correctly"

    def test_execute_tool_calls(self):
        """Test executing multiple tool calls concurrently"""
        # Mock tool map and tools
        tool_map = {
            'tool1': lambda x: f"tool1 processed {x}",
            'tool2': lambda x: f"tool2 processed {x}"
        }
        tools = ['tool1', 'tool2']

        tool_calls = [
            {"function": {"name": "tool1", "arguments": {"x": "data1"}}},
            {"function": {"name": "tool2", "arguments": {"x": "data2"}}}
        ]

        results = execute_tool_calls(tool_calls, tool_map, tools)

        assert len(results) == 2, "Incorrect number of results returned"
        assert results[0][3] == "tool1 processed data1", "First tool call result is incorrect"
        assert results[1][3] == "tool2 processed data2", "Second tool call result is incorrect"