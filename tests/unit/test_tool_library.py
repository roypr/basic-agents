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

    # Cleanup
    run_command("rm -rf src")