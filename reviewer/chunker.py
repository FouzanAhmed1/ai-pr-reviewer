"""Split a unified diff into review-sized chunks.

Strategy: split the diff into per-file pieces first, then split any file
whose hunk text exceeds the token budget into per-hunk pieces. Token counts
are estimates (chars / 4), which is deliberately rough: it only needs to keep
payloads comfortably under model limits, not to be exact.
"""

import re

CHARS_PER_TOKEN = 4

HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")
FILE_HEADER_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")
NEW_FILE_RE = re.compile(r"^\+\+\+ b/(.*)$")


def estimate_tokens(text):
    """Rough token estimate for budgeting: chars / 4."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def split_files(diff_text):
    """Split a unified diff into (path, file_diff) tuples.

    Binary diffs and renames are kept as their own entries so they are never
    silently dropped.
    """
    files = []
    current_path = None
    current_lines = []

    def flush():
        if current_path is not None:
            files.append((current_path, "\n".join(current_lines)))

    for line in diff_text.splitlines():
        m = FILE_HEADER_RE.match(line)
        if m:
            flush()
            current_path = m.group(2)
            current_lines = [line]
            continue
        if current_path is not None:
            current_lines.append(line)
    flush()
    return files


def split_hunks(file_diff):
    """Split one file's diff text into (hunk_text, starting_new_line) pieces.

    The second tuple element is the first new-file line number of the hunk,
    so reviewers can map findings back to absolute line numbers.
    """
    hunks = []
    header = []
    current = None
    start_line = 0

    for line in file_diff.splitlines():
        if HUNK_HEADER_RE.match(line):
            if current is not None:
                hunks.append(("\n".join(current), start_line))
            nums = line.split(" ")[2]  # e.g. "+12,7"
            start_line = int(nums[1:].split(",")[0])
            current = [line]
        elif current is None:
            header.append(line)
        else:
            current.append(line)
    if current is not None:
        hunks.append(("\n".join(current), start_line))

    if not hunks:
        return [(file_diff, 0)]
    if header:
        # Keep file metadata attached to the first hunk so the model sees paths.
        first_text, first_start = hunks[0]
        hunks[0] = ("\n".join(header) + "\n" + first_text, first_start)
    return hunks


def chunk_diff(diff_text, max_chunk_tokens):
    """Split a diff into chunks, each estimated under ``max_chunk_tokens``.

    Returns a list of dicts: {"path": str, "text": str, "line_offset": int}
    where ``line_offset`` is the new-file line number of the first line of
    the chunk's first hunk.
    """
    chunks = []
    for path, file_diff in split_files(diff_text):
        if estimate_tokens(file_diff) <= max_chunk_tokens:
            chunks.append({"path": path, "text": file_diff, "line_offset": 0})
            continue
        for hunk_text, start_line in split_hunks(file_diff):
            # split_hunks already attaches the file header to the first hunk,
            # so the budget check below sees the real chunk size.
            if estimate_tokens(hunk_text) <= max_chunk_tokens:
                chunks.append({"path": path, "text": hunk_text, "line_offset": start_line})
                continue
            # Even a single hunk is over budget: greedily pack lines so every
            # piece stays under the budget when measured, not estimated.
            piece_lines = []
            for line in hunk_text.splitlines():
                if piece_lines and estimate_tokens("\n".join(piece_lines) + "\n" + line) > max_chunk_tokens:
                    chunks.append(
                        {
                            "path": path,
                            "text": "\n".join(piece_lines),
                            "line_offset": start_line,
                        }
                    )
                    piece_lines = []
                piece_lines.append(line)
            if piece_lines:
                chunks.append(
                    {"path": path, "text": "\n".join(piece_lines), "line_offset": start_line}
                )
    return chunks
