"""Tests for diff chunking."""

from reviewer.chunker import chunk_diff, estimate_tokens, split_files, split_hunks

SMALL_DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
 def add(a, b):
-    return a + b
+    # fast path
+    return a + b
     return None
"""


def make_big_diff(num_files=3, lines_per_file=600):
    parts = []
    for i in range(num_files):
        body = "\n".join(f"+line {j} with some content to fill space" for j in range(lines_per_file))
        parts.append(
            f"diff --git a/src/f{i}.py b/src/f{i}.py\n"
            f"index 1111111..2222222 100644\n"
            f"--- a/src/f{i}.py\n"
            f"+++ b/src/f{i}.py\n"
            f"@@ -1,{lines_per_file} +1,{lines_per_file} @@\n"
            f"{body}\n"
        )
    return "\n".join(parts)


def test_estimate_tokens_is_chars_over_four():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("") == 1  # never zero


def test_small_diff_stays_whole():
    chunks = chunk_diff(SMALL_DIFF, max_chunk_tokens=4000)
    assert len(chunks) == 1
    assert chunks[0]["path"] == "src/app.py"
    assert "+    return a + b" in chunks[0]["text"]


def test_large_diff_splits_into_multiple_chunks():
    diff = make_big_diff(num_files=3, lines_per_file=600)
    chunks = chunk_diff(diff, max_chunk_tokens=1000)
    assert len(chunks) > 3  # per-hunk splitting, not just per-file
    for chunk in chunks:
        assert estimate_tokens(chunk["text"]) <= 1000
        assert chunk["path"] in {f"src/f{i}.py" for i in range(3)}


def test_each_file_kept_whole_when_it_fits():
    diff = make_big_diff(num_files=2, lines_per_file=10)
    chunks = chunk_diff(diff, max_chunk_tokens=4000)
    assert len(chunks) == 2
    paths = {c["path"] for c in chunks}
    assert paths == {"src/f0.py", "src/f1.py"}


def test_split_files_returns_paths():
    files = split_files(SMALL_DIFF + make_big_diff(num_files=1, lines_per_file=2))
    assert [p for p, _ in files] == ["src/app.py", "src/f0.py"]


def test_split_hunks_reports_start_line():
    hunks = split_hunks(SMALL_DIFF)
    assert len(hunks) == 1
    text, start_line = hunks[0]
    assert start_line == 1
    assert "@@" in text


def test_empty_diff_yields_no_chunks():
    assert chunk_diff("", max_chunk_tokens=4000) == []
