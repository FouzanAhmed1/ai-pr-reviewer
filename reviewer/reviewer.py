"""Orchestrates the full review: fetch -> chunk -> review -> post."""

import logging

from . import github_client, llm
from .chunker import chunk_diff, estimate_tokens
from .parser import ParseError, build_summary, format_comment, parse_findings

log = logging.getLogger(__name__)


def review_pull_request(config, repo, pr_number, dry_run=False):
    """Run the review pipeline for one PR.

    Returns a dict with findings, the formatted comments, and whether the
    review was posted. Raises GitHubError / LLMError / ConfigError on fatal
    failures; malformed LLM JSON for a chunk is retried once then skipped
    with a warning.
    """
    pr = github_client.get_pull_request(config.github_token, repo, pr_number)
    log.info("Reviewing PR #%s: %s", pr_number, pr["title"])

    diff = github_client.get_diff(config.github_token, repo, pr_number)
    if not diff.strip():
        log.warning("PR #%s has an empty diff; nothing to review.", pr_number)
        return {"findings": [], "comments": [], "posted": False, "skipped_chunks": 0}

    chunks = chunk_diff(diff, config.max_chunk_tokens)
    log.info(
        "Diff split into %d chunk(s) (~%d estimated tokens total).",
        len(chunks),
        estimate_tokens(diff),
    )

    findings = []
    skipped_chunks = 0
    for i, chunk in enumerate(chunks, 1):
        log.info("Reviewing chunk %d/%d: %s", i, len(chunks), chunk["path"])
        raw = llm.review_chunk(
            config.llm_base_url,
            config.llm_api_key,
            config.llm_model,
            pr,
            chunk["text"],
        )
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
            )
            try:
                findings.extend(parse_findings(raw))
            except ParseError:
                log.warning("Chunk %d/%d still malformed; skipping.", i, len(chunks))
                skipped_chunks += 1

    comments = [
        {"path": f["path"], "line": f["line"], "body": format_comment(f)} for f in findings
    ]
    summary = build_summary(findings, skipped_chunks)

    posted = False
    if not dry_run:
        github_client.post_review(
            config.github_token, repo, pr_number, comments, summary_body=summary
        )
        posted = True
        log.info("Posted review on PR #%s with %d inline comment(s).", pr_number, len(comments))
    else:
        log.info("Dry run: not posting. %d comment(s) generated.", len(comments))

    return {
        "findings": findings,
        "comments": comments,
        "summary": summary,
        "posted": posted,
        "skipped_chunks": skipped_chunks,
    }
