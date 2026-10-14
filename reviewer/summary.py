"""Build the single top-level PR summary comment for a review run.

The comment carries an HTML marker so re-runs can find and update the
existing summary instead of posting a duplicate.
"""

from .config import SEVERITIES

#: Marker embedded in the summary comment; used to find it on re-runs.
SUMMARY_MARKER = "<!-- ai-pr-reviewer-summary -->"


def build_summary_comment(files_reviewed, findings, cost, dimensions,
                          incremental=False, commits_reviewed=0):
    """Render the summary comment body.

    ``cost`` is the dict from ``CostTracker.as_dict()``. ``dimensions`` is
    the list of enabled review dimensions.
    """
    lines = [SUMMARY_MARKER, "## AI PR review summary", ""]
    lines.append(f"Files reviewed: {files_reviewed}")

    counts = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    if counts:
        parts = [f"{counts[s]} {s}" for s in SEVERITIES if counts.get(s)]
        lines.append(f"Findings: {len(findings)} ({', '.join(parts)})")
    else:
        lines.append("Findings: none")

    lines.append(
        f"Estimated cost: ~${cost['cost_usd']:.4f} "
        f"({cost['prompt_tokens']:,} prompt + {cost['completion_tokens']:,} completion "
        f"tokens, {cost['model']})"
    )
    lines.append("Review dimensions: " + ", ".join(dimensions))
    if incremental:
        lines.append(
            f"Incremental review: {commits_reviewed} new commit(s) since the last review."
        )
    return "\n".join(lines)
