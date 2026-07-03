"""Unit tests for history_compressor stripping logic."""

from utils.history_compressor import strip_message, strip_history


class TestStripMessage:
    """Tests for strip_message pure function."""

    def test_strip_system_message(self):
        """System messages pass through unchanged."""
        msg = {"role": "system", "content": "You are a helpful assistant."}
        result = strip_message(msg)
        assert result == msg

    def test_strip_user_message(self):
        """User messages pass through unchanged."""
        msg = {"role": "user", "content": "Hello!"}
        result = strip_message(msg)
        assert result == msg

    def test_strip_assistant_message_no_tool_calls(self):
        """Assistant messages without tool_calls pass through unchanged."""
        msg = {"role": "assistant", "content": "Sure, I can help!"}
        result = strip_message(msg)
        assert result == msg

    def test_strip_assistant_message_with_tool_calls(self):
        """Assistant messages with tool_calls get tool_calls dropped."""
        msg = {
            "role": "assistant",
            "content": "Let me search for that.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query": "test"}',
                    },
                }
            ],
        }
        result = strip_message(msg)
        assert result == {"role": "assistant", "content": "Let me search for that."}
        assert "tool_calls" not in result

    def test_strip_tool_message(self):
        """Tool messages are removed entirely."""
        msg = {
            "role": "tool",
            "content": "Result: found 42 results",
            "tool_call_id": "call_123",
        }
        result = strip_message(msg)
        assert result is None

    def test_strip_unknown_role(self):
        """Unknown roles are kept as-is."""
        msg = {"role": "system", "content": "custom"}
        result = strip_message(msg)
        assert result == msg


class TestStripHistory:
    """Tests for strip_history on message lists."""

    def test_strip_empty_list(self):
        """Empty list returns empty list."""
        assert strip_history([]) == []

    def test_strip_simple_conversation(self):
        """System + user + assistant with no tool calls passes through."""
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        result = strip_history(messages)
        assert result == messages

    def test_strip_conversation_with_tool_calls(self):
        """Tool messages removed, assistant tool_calls stripped."""
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Search for X"},
            {
                "role": "assistant",
                "content": "Searching...",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"q": "X"}'},
                    }
                ],
            },
            {"role": "tool", "content": '{"results": []}', "tool_call_id": "call_1"},
            {
                "role": "assistant",
                "content": "Here are the results.",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "finish", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "content": "Done", "tool_call_id": "call_2"},
        ]
        result = strip_history(messages)
        expected = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Search for X"},
            {"role": "assistant", "content": "Searching..."},
            {"role": "assistant", "content": "Here are the results."},
        ]
        assert result == expected
        for msg in result:
            assert "tool_calls" not in msg, f"tool_calls found in {msg}"

    def test_strip_only_tool_messages(self):
        """Only tool messages exist -> empty result."""
        messages = [
            {"role": "tool", "content": "result 1", "tool_call_id": "c1"},
            {"role": "tool", "content": "result 2", "tool_call_id": "c2"},
        ]
        assert strip_history(messages) == []
