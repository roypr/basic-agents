import pytest

from core.agent_notes import (
    MAX_INJECTED_NOTES,
    MAX_NOTE_CHARS,
    AgentNotes,
)


@pytest.mark.unit
class TestBuildSystemPrompt:
    def test_notes_are_appended_to_agent_prompt(self, tmp_path):
        from core.base_agent import BaseAgent

        agent = BaseAgent.__new__(BaseAgent)
        agent.system_prompt = "You are a test agent."
        agent.notes = AgentNotes("code", notes_dir=tmp_path)
        assert agent._build_system_prompt() == "You are a test agent."

        agent.notes.note_to_self("read_lines is 1-based")
        prompt = agent._build_system_prompt()
        assert prompt.startswith("You are a test agent.")
        assert "- read_lines is 1-based" in prompt


@pytest.mark.unit
class TestAgentNotes:
    def test_saves_note_to_per_agent_file(self, tmp_path):
        notes = AgentNotes("code", notes_dir=tmp_path)

        assert (
            notes.note_to_self("grep_search ignores glob when path is a file")
            == "Note saved."
        )
        assert (tmp_path / "code.txt").read_text(encoding="utf-8") == (
            "grep_search ignores glob when path is a file\n"
        )

    def test_agents_have_separate_files(self, tmp_path):
        AgentNotes("code", notes_dir=tmp_path).note_to_self("a")
        AgentNotes("plan", notes_dir=tmp_path).note_to_self("b")

        assert AgentNotes("code", notes_dir=tmp_path).render().endswith("- a")
        assert AgentNotes("plan", notes_dir=tmp_path).render().endswith("- b")

    def test_empty_note_is_rejected(self, tmp_path):
        notes = AgentNotes("code", notes_dir=tmp_path)

        assert notes.note_to_self("   \n ") == "Error: note is empty"
        assert not notes.path.exists()

    def test_duplicate_note_is_not_stored_twice(self, tmp_path):
        notes = AgentNotes("code", notes_dir=tmp_path)
        notes.note_to_self("file_edit needs unique old_str")

        assert (
            notes.note_to_self("File_Edit needs  unique old_str")
            == "Note already recorded."
        )
        assert notes.path.read_text(encoding="utf-8").count("\n") == 1

    def test_multiline_note_is_collapsed_to_one_line(self, tmp_path):
        notes = AgentNotes("code", notes_dir=tmp_path)
        notes.note_to_self("line one\nline two")

        assert notes.path.read_text(encoding="utf-8") == "line one line two\n"

    def test_long_note_is_truncated(self, tmp_path):
        notes = AgentNotes("code", notes_dir=tmp_path)
        notes.note_to_self("x" * (MAX_NOTE_CHARS + 50))

        stored = notes.path.read_text(encoding="utf-8").rstrip("\n")
        assert len(stored) == MAX_NOTE_CHARS

    def test_render_empty_when_no_notes(self, tmp_path):
        assert AgentNotes("code", notes_dir=tmp_path).render() == ""

    def test_render_injects_only_most_recent_notes(self, tmp_path):
        notes = AgentNotes("code", notes_dir=tmp_path)
        total = MAX_INJECTED_NOTES + 5
        for i in range(total):
            notes.note_to_self(f"note {i}")

        rendered = notes.render()
        assert "- note 0\n" not in rendered
        assert f"- note {total - 1}" in rendered
        assert rendered.count("\n- ") == MAX_INJECTED_NOTES

    def test_render_marks_notes_as_not_instructions(self, tmp_path):
        notes = AgentNotes("code", notes_dir=tmp_path)
        notes.note_to_self("tool x returns empty on timeout")

        rendered = notes.render()
        assert "## Notes to self" in rendered
        assert "not user instructions" in rendered
