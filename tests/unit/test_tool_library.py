from utils.tools_library import grep_search, get_all_files


def test_grep_search(monkeypatch, tmp_path):
    monkeypatch.setattr("utils.file_utils.FILES_BASE_DIR", str(tmp_path))
    search_dir = tmp_path / "test_grep"
    search_dir.mkdir()
    (search_dir / "a.txt").write_text("hello world\n", encoding="utf-8")
    (search_dir / "b.txt").write_text("hello python\n", encoding="utf-8")
    (search_dir / "c.txt").write_text("goodbye world\n", encoding="utf-8")

    print("1. files_with_matches:")
    print(grep_search("hello", path="test_grep"))

    print("\n2. content mode:")
    print(grep_search("hello", path="test_grep", output_mode="content"))

    print("\n3. count mode:")
    print(grep_search("hello", path="test_grep", output_mode="count"))

    print("\n4. no matches (should return []):")
    print(grep_search("zzznomatch", path="test_grep"))

    print("\n5. case insensitive:")
    print(grep_search("HELLO", path="test_grep", case_insensitive=True))

    print("\n6. glob filter (only .txt):")
    print(grep_search("hello", path="test_grep", glob="*.txt"))

    print("\n7. bad pattern (should return error string):")
    print(grep_search("[invalid", path="test_grep"))


def test_get_all_files(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr("utils.file_utils.FILES_BASE_DIR", str(tmp_path))
    (tmp_path / "src" / "code").mkdir(parents=True)
    (tmp_path / "src" / "code" / "a.txt").write_text("hello world", encoding="utf-8")
    (tmp_path / "src" / "code" / "b.txt").write_text("hello world", encoding="utf-8")

    result = json.loads(get_all_files("src"))
    print(result)
    assert result == ["code/a.txt", "code/b.txt"]


def test_read_image(monkeypatch, tmp_path):
    """read_image returns structured JSON (type/mime/data) for a valid image."""
    import base64
    import json

    from utils import file_utils
    from utils.tools_library import read_image

    # 1x1 transparent PNG
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    img = tmp_path / "pixel.png"
    img.write_bytes(png)

    # safe_path resolves against file_utils.FILES_BASE_DIR
    monkeypatch.setattr(file_utils, "FILES_BASE_DIR", str(tmp_path))

    out = read_image("pixel.png")
    parsed = json.loads(out)
    assert parsed["type"] == "image"
    assert parsed["mime"] == "image/png"
    assert parsed["data"] == base64.b64encode(png).decode("utf-8")
    assert parsed["path"] == "pixel.png"


def test_read_image_missing(monkeypatch, tmp_path):
    """read_image returns an error string for a missing file."""
    from utils import file_utils
    from utils.tools_library import read_image

    monkeypatch.setattr(file_utils, "FILES_BASE_DIR", str(tmp_path))

    out = read_image("missing.png")
    assert out.startswith("Error:")
