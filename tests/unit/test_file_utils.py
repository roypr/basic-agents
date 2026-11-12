import pytest

from utils.file_utils import _file_edit, _replace_lines, load_tool_definition


def test_file_edit_requires_unique_existing_anchor(tmp_path, monkeypatch):
    from utils import file_utils

    monkeypatch.setattr(file_utils, "FILES_BASE_DIR", str(tmp_path))
    path = tmp_path / "edit.txt"
    path.write_text("first\nsecond\n", encoding="utf-8")

    with pytest.raises(ValueError, match="old_str not found"):
        _file_edit("edit.txt", "replacement", "missing")


def test_file_edit_rejects_ambiguous_anchor(tmp_path, monkeypatch):
    from utils import file_utils

    monkeypatch.setattr(file_utils, "FILES_BASE_DIR", str(tmp_path))
    (tmp_path / "edit.txt").write_text("same same", encoding="utf-8")

    with pytest.raises(ValueError, match="matches multiple places"):
        _file_edit("edit.txt", "replacement", "same")


def test_replace_lines_returns_removed_text_and_preview(tmp_path, monkeypatch):
    from utils import file_utils

    monkeypatch.setattr(file_utils, "FILES_BASE_DIR", str(tmp_path))
    path = tmp_path / "lines.txt"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    result = _replace_lines("lines.txt", 2, "new", 3)

    assert "--- removed (first 5 lines) ---\n2: two\n3: three\n" in result
    assert "--- result ---" in result
    assert path.read_text(encoding="utf-8") == "one\nnew\nfour\n"


def test_replace_lines_rejects_end_after_eof(tmp_path, monkeypatch):
    from utils import file_utils

    monkeypatch.setattr(file_utils, "FILES_BASE_DIR", str(tmp_path))
    (tmp_path / "lines.txt").write_text("one\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exceeds file length"):
        _replace_lines("lines.txt", 1, "new", 2)


def test_load_tool_definition_returns_dict():
    """
    Test that the function returns a dictionary.
    """
    result = load_tool_definition()
    assert isinstance(result, dict), "The function should return a dictionary."
