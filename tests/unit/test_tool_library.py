from utils.tools_library import grep_search, run_command, get_all_files


def test_grep_search():
    # Setup: create test files
    run_command("mkdir -p test_grep")
    run_command("echo 'hello world' > test_grep/a.txt")
    run_command("echo 'hello python' > test_grep/b.txt")
    run_command("echo 'goodbye world' > test_grep/c.txt")

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

    # Cleanup
    run_command("rm -rf test_grep")


def test_get_all_files():
    run_command("mkdir -p src/code")
    run_command("echo 'hello world' > src/code/a.txt")
    run_command("echo 'hello world' > src/code/b.txt")

    result = get_all_files("src")
    print(result)
    assert isinstance(result, dict), "The function should return a dictionary."


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
