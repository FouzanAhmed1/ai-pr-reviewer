"""GitHub REST API client for fetching PR diffs and posting review comments."""

import requests

from .summary import SUMMARY_MARKER

GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT = 30


class GitHubError(Exception):
    """Raised when a GitHub API call fails."""


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_pull_request(token, repo, pr_number):
    """Fetch PR metadata (title, body, head sha) for review context."""
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}"
    try:
        resp = requests.get(url, headers=_headers(token), timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GitHubError(f"Failed to reach GitHub API for PR #{pr_number}: {exc}") from exc
    if resp.status_code != 200:
        raise GitHubError(
            f"GitHub API returned {resp.status_code} fetching PR #{pr_number}: {resp.text[:200]}"
        )
    data = resp.json()
    return {
        "title": data.get("title", ""),
        "body": data.get("body") or "",
        "head_sha": data.get("head", {}).get("sha", ""),
    }


def get_diff(token, repo, pr_number):
    """Fetch the unified diff of a PR as raw text.

    Returns an empty string when the PR has no changes.
    """
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}"
    headers = _headers(token)
    headers["Accept"] = "application/vnd.github.diff"
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GitHubError(f"Failed to fetch diff for PR #{pr_number}: {exc}") from exc
    if resp.status_code == 204:
        return ""
    if resp.status_code != 200:
        raise GitHubError(
            f"GitHub API returned {resp.status_code} fetching diff for PR #{pr_number}: {resp.text[:200]}"
        )
    return resp.text or ""


def list_pr_commits(token, repo, pr_number):
    """List commit SHAs on a PR, oldest first."""
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/commits"
    try:
        resp = requests.get(url, headers=_headers(token), timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GitHubError(f"Failed to list commits for PR #{pr_number}: {exc}") from exc
    if resp.status_code != 200:
        raise GitHubError(
            f"GitHub API returned {resp.status_code} listing commits for PR #{pr_number}: {resp.text[:200]}"
        )
    try:
        return [c["sha"] for c in resp.json()]
    except (ValueError, KeyError, TypeError) as exc:
        raise GitHubError(f"Could not parse commit list for PR #{pr_number}: {exc}") from exc


def get_compare_diff(token, repo, base_sha, head_sha):
    """Unified diff of the commits after ``base_sha`` up to ``head_sha``.

    Used for incremental reviews. Raises GitHubError when the compare fails
    (e.g. a force-push made ``base_sha`` unknown); the caller can fall back
    to the full PR diff.
    """
    url = f"{GITHUB_API}/repos/{repo}/compare/{base_sha}...{head_sha}"
    headers = _headers(token)
    headers["Accept"] = "application/vnd.github.diff"
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GitHubError(
            f"Failed to compare {base_sha[:7]}...{head_sha[:7]}: {exc}"
        ) from exc
    if resp.status_code != 200:
        raise GitHubError(
            f"GitHub API returned {resp.status_code} comparing "
            f"{base_sha[:7]}...{head_sha[:7]}: {resp.text[:200]}"
        )
    return resp.text or ""


def post_review(token, repo, pr_number, comments, summary_body, event="COMMENT"):
    """Post an inline review (one comment per finding) on a pull request.

    Each comment dict needs: path, line (int), body. ``line`` must refer to a
    line that exists in the PR diff.
    """
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews"
    payload = {
        "event": event,
        "body": summary_body,
        "comments": [
            {"path": c["path"], "line": c["line"], "body": c["body"]} for c in comments
        ],
    }
    try:
        resp = requests.post(url, headers=_headers(token), json=payload, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GitHubError(f"Failed to post review on PR #{pr_number}: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise GitHubError(
            f"GitHub API returned {resp.status_code} posting review on PR #{pr_number}: {resp.text[:300]}"
        )
    return resp.json()


def list_issue_comments(token, repo, issue_number):
    """List comments on a PR (PRs are issues under the hood): [{id, body}]."""
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
    try:
        resp = requests.get(url, headers=_headers(token), timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GitHubError(f"Failed to list comments on PR #{issue_number}: {exc}") from exc
    if resp.status_code != 200:
        raise GitHubError(
            f"GitHub API returned {resp.status_code} listing comments on PR #{issue_number}: {resp.text[:200]}"
        )
    try:
        return [{"id": c["id"], "body": c.get("body") or ""} for c in resp.json()]
    except (ValueError, KeyError, TypeError) as exc:
        raise GitHubError(f"Could not parse comments on PR #{issue_number}: {exc}") from exc


def create_issue_comment(token, repo, issue_number, body):
    """Post a new top-level comment on a PR."""
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
    try:
        resp = requests.post(url, headers=_headers(token), json={"body": body}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GitHubError(f"Failed to comment on PR #{issue_number}: {exc}") from exc
    if resp.status_code != 201:
        raise GitHubError(
            f"GitHub API returned {resp.status_code} commenting on PR #{issue_number}: {resp.text[:300]}"
        )
    return resp.json()


def update_issue_comment(token, repo, comment_id, body):
    """Edit an existing top-level comment (used to update the summary)."""
    url = f"{GITHUB_API}/repos/{repo}/issues/comments/{comment_id}"
    try:
        resp = requests.patch(url, headers=_headers(token), json={"body": body}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise GitHubError(f"Failed to update comment {comment_id}: {exc}") from exc
    if resp.status_code != 200:
        raise GitHubError(
            f"GitHub API returned {resp.status_code} updating comment {comment_id}: {resp.text[:300]}"
        )
    return resp.json()


def post_or_update_summary_comment(token, repo, pr_number, body):
    """Post the summary comment, or update the existing one in place.

    The existing summary is found by its HTML marker, so re-runs never
    duplicate it.
    """
    for comment in list_issue_comments(token, repo, pr_number):
        if SUMMARY_MARKER in comment["body"]:
            return update_issue_comment(token, repo, comment["id"], body)
    return create_issue_comment(token, repo, pr_number, body)
