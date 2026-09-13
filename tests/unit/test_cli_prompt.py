import pytest

from cli import prompt
from core.permissions import PermissionRequest


@pytest.mark.unit
class TestPromptRendering:
    def test_prompt_includes_tool_and_args(self):
        request = PermissionRequest(
            tool_name="file_write",
            args={"path": "a.txt", "content": "hello"},
            tool_call_id="call_1",
        )

        rendered = prompt.render_prompt(request)

        assert "file_write" in rendered
        assert "path: a.txt" in rendered
        assert "content: hello" in rendered

    def test_prompt_labels_always_options_with_scope(self):
        request = PermissionRequest(tool_name="run_command", args={"command": "ls"})

        rendered = prompt.render_prompt(request, scope="session")

        assert "allow always (this session)" in rendered
        assert "deny always (this session)" in rendered
        # Once options carry no scope label.
        assert "allow once" in rendered
        assert "deny once" in rendered

    def test_empty_args_rendered_explicitly(self):
        assert prompt.format_args({}) == "    (no arguments)"

    def test_long_values_are_truncated(self):
        long_value = "x" * 500

        rendered = prompt.truncate(long_value, max_len=20)

        assert len(rendered) == 20
        assert rendered.endswith("…")

    def test_non_string_values_are_json_encoded(self):
        rendered = prompt.truncate({"a": 1}, max_len=100)

        assert rendered == '{"a": 1}'

    def test_newlines_collapsed_to_single_line(self):
        rendered = prompt.truncate("line1\nline2", max_len=100)

        assert "\n" not in rendered
        assert rendered == "line1 line2"

    def test_render_choices_matches_resolver_keys(self):
        choices = prompt.render_choices()

        for key in ("[o]", "[a]", "[d]", "[n]", "[m]"):
            assert key in choices
