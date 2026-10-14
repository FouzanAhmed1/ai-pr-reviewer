"""GitHub REST API client for fetching PR diffs and posting review comments."""

import requests

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
