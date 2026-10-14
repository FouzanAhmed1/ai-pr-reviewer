"""Orchestrates the full review: fetch -> chunk -> review -> post."""

import fnmatch
import logging
import os

from . import github_client, llm
from . import cost as cost_mod
from . import state as state_mod
from .chunker import chunk_diff, estimate_tokens
from .config import SEVERITY_RANK
from .parser import CATEGORIES, ParseError, build_summary, format_comment, parse_findings

log = logging.getLogger(__name__)

#: File extension -> language key used by the ``per_language`` config section.
EXTENSION_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".sh": "shell",
}


def is_ignored(path, ignore_patterns):
    """True when ``path`` matches any glob in ``ignore_patterns``."""
    return any(fnmatch.fnmatch(path, pattern) for pattern in ignore_patterns)


def language_for_path(path):
    """Language key for a file path, or None when unknown."""
    return EXTENSION_LANGUAGES.get(os.path.splitext(path)[1].lower())


def make_budget_for(config):
    """Build the per-file chunk-budget callable from the config.

    Languages listed under ``per_language`` with a ``max_chunk_tokens`` get
    their own budget; every other file uses ``config.max_chunk_tokens``.
    """
    overrides = {
        lang: opts.get("max_chunk_tokens")
        for lang, opts in config.per_language.items()
    }

    def budget_for(path):
        override = overrides.get(language_for_path(path))
        return override or config.max_chunk_tokens

    return budget_for


def apply_finding_filters(findings, config):
    """Drop findings in disabled dimensions or below the severity threshold."""
    enabled = {c for c, on in config.review_rules.items() if on}
    filtered = [f for f in findings if f["category"] in enabled]
    if config.severity_threshold:
        min_rank = SEVERITY_RANK[config.severity_threshold]
        filtered = [f for f in filtered if SEVERITY_RANK[f["severity"]] >= min_rank]
    return filtered


def enabled_dimensions(config):
    """Review dimensions enabled by the config, in canonical order."""
    return [c for c in CATEGORIES if config.review_rules.get(c, True)]


def _resolve_diff(token, repo, pr_number, state, full):
    """Pick which diff to review.

    Returns (diff_text, incremental, commits_reviewed, head_sha). ``diff_text``
    is None when the PR was already reviewed through its head commit and there
    is nothing new to review.
    """
    commits = github_client.list_pr_commits(token, repo, pr_number)
    head_sha = commits[-1] if commits else ""
    last_sha = None if full else state_mod.get_last_reviewed_sha(state, repo, pr_number)

    if last_sha and last_sha in commits:
        new_commits = commits[commits.index(last_sha) + 1:]
        if not new_commits:
            log.info(
                "PR #%s already reviewed through %s; nothing new to review.",
                pr_number,
                last_sha[:7],
            )
            return None, True, 0, head_sha
        log.info(
            "Incremental review: %d new commit(s) since %s.",
            len(new_commits),
            last_sha[:7],
        )
        try:
            diff = github_client.get_compare_diff(token, repo, last_sha, head_sha)
        except github_client.GitHubError as exc:
            log.warning("%s; falling back to the full PR diff.", exc)
            diff = github_client.get_diff(token, repo, pr_number)
            return diff, False, len(commits), head_sha
        return diff, True, len(new_commits), head_sha

    if full:
        log.info("--full given: re-reviewing the whole PR.")
    elif last_sha:
        log.info(
            "Last reviewed SHA %s is not in the PR commits (maybe force-pushed); "
            "reviewing the full PR.",
            last_sha[:7],
        )
    return github_client.get_diff(token, repo, pr_number), False, len(commits), head_sha


def review_pull_request(config, repo, pr_number, dry_run=False,
                        state_path=state_mod.DEFAULT_STATE_PATH, full=False):
    """Run the review pipeline for one PR.

    On a re-run only commits pushed after the last reviewed SHA (recorded in
    ``state_path``) are reviewed, unless ``full`` is true. The state file is
    updated whenever the run completes, dry runs included, so a later real
    run only covers new commits.

    Returns a dict with findings, the formatted comments, and whether the
    review was posted. Raises GitHubError / LLMError / ConfigError on fatal
    failures; malformed LLM JSON for a chunk is retried once then skipped
    with a warning.
    """
    pr = github_client.get_pull_request(config.github_token, repo, pr_number)
    log.info("Reviewing PR #%s: %s", pr_number, pr["title"])

    state = state_mod.load_state(state_path)
    diff, incremental, commits_reviewed, head_sha = _resolve_diff(
        config.github_token, repo, pr_number, state, full
    )

    base_result = {
        "findings": [],
        "comments": [],
        "posted": False,
        "skipped_chunks": 0,
        "incremental": incremental,
        "commits_reviewed": commits_reviewed,
        "up_to_date": False,
    }

    if diff is None:
        # Nothing new since the last review.
        summary = (
            f"AI review: PR #{pr_number} is already up to date "
            f"(reviewed through {head_sha[:7]})."
        )
        return {**base_result, "summary": summary, "up_to_date": True}

    if not diff.strip():
        log.warning("PR #%s has an empty diff; nothing to review.", pr_number)
        _record_state(state, state_path, repo, pr_number, head_sha)
        summary = f"AI review: PR #{pr_number} has an empty diff; nothing to review."
        return {**base_result, "summary": summary}

    chunks = chunk_diff(diff, config.max_chunk_tokens, make_budget_for(config))
    chunks = [c for c in chunks if not is_ignored(c["path"], config.ignore_paths)]
    if config.ignore_paths and not chunks:
        log.info("All diff chunks matched ignore_paths; nothing to review.")
    log.info(
        "Diff split into %d chunk(s) (~%d estimated tokens total).",
        len(chunks),
        estimate_tokens(diff),
    )

    dimensions = enabled_dimensions(config)
    tracker = cost_mod.CostTracker(config.llm_model, config.pricing)
    findings = []
    skipped_chunks = 0
    for i, chunk in enumerate(chunks, 1):
        log.info("Reviewing chunk %d/%d: %s", i, len(chunks), chunk["path"])
        messages = llm.build_messages(pr, chunk["text"], dimensions)
        prompt_tokens = cost_mod.count_tokens(
            "\n".join(m["content"] for m in messages), config.llm_model
        )
        raw = llm.review_chunk(
            config.llm_base_url,
            config.llm_api_key,
            config.llm_model,
            pr,
            chunk["text"],
            enabled_categories=dimensions,
        )
        tracker.record(prompt_tokens, cost_mod.count_tokens(raw or "", config.llm_model))
        try:
            findings.extend(parse_findings(raw))
        except ParseError:
            log.warning("Chunk %d/%d returned malformed JSON; retrying once.", i, len(chunks))
            raw = llm.review_chunk(
                config.llm_base_url,
                config.llm_api_key,
                config.llm_model,
                pr,
                chunk["text"],
                enabled_categories=dimensions,
            )
            tracker.record(0, cost_mod.count_tokens(raw or "", config.llm_model))
            try:
                findings.extend(parse_findings(raw))
            except ParseError:
                log.warning("Chunk %d/%d still malformed; skipping.", i, len(chunks))
                skipped_chunks += 1

    findings = apply_finding_filters(findings, config)
    cost_line = tracker.summary_line()
    log.info(cost_line)

    comments = [
        {"path": f["path"], "line": f["line"], "body": format_comment(f)} for f in findings
    ]
    summary = build_summary(findings, skipped_chunks) + "\n" + cost_line

    posted = False
    if not dry_run:
        github_client.post_review(
            config.github_token, repo, pr_number, comments, summary_body=summary
        )
        posted = True
        log.info("Posted review on PR #%s with %d inline comment(s).", pr_number, len(comments))
    else:
        log.info("Dry run: not posting. %d comment(s) generated.", len(comments))

    _record_state(state, state_path, repo, pr_number, head_sha)

    return {
        **base_result,
        "findings": findings,
        "comments": comments,
        "summary": summary,
        "posted": posted,
        "skipped_chunks": skipped_chunks,
        "cost": tracker.as_dict(),
        "cost_line": cost_line,
    }


def _record_state(state, state_path, repo, pr_number, head_sha):
    """Persist the reviewed head SHA so the next run is incremental."""
    if not head_sha:
        return
    state_mod.set_last_reviewed_sha(state, repo, pr_number, head_sha)
    try:
        state_mod.save_state(state_path, state)
    except OSError as exc:
        log.warning("Could not write state file %s: %s", state_path, exc)
