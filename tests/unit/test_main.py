import pytest
from pathlib import Path

import main


def test_parse_line_range_single_value():
    assert main.parse_line_range("5") == (1, 5)


def test_parse_line_range_range_value():
    assert main.parse_line_range("10-20") == (10, 20)


def test_parse_line_range_invalid_format():
    with pytest.raises(ValueError):
        main.parse_line_range("foo")


def test_build_query_with_whole_file(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("first\nsecond\nthird\n", encoding="utf-8")

    combined = main.build_query("Summarize this file:", str(sample), None)

    assert "Summarize this file:" in combined
    assert "Included file content from" in combined
    assert "first\nsecond\nthird\n" in combined
    assert "Whole file" in combined


def test_build_query_with_line_range(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    combined = main.build_query("Read this segment:", str(sample), (2, 3))

    assert "line2\nline3\n" in combined
    assert "Lines 2-3" in combined


def test_build_query_without_query_only_include(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("alpha\nbeta\n", encoding="utf-8")

    combined = main.build_query("", str(sample), None)

    assert combined.startswith("Included file content from")
    assert "alpha\nbeta\n" in combined


def test_read_file_section_line_range_exceeds_file(tmp_path):
    sample = tmp_path / "sample.txt"
    sample.write_text("one\ntwo\n", encoding="utf-8")

    with pytest.raises(ValueError):
        main.read_file_section(str(sample), (1, 5))
