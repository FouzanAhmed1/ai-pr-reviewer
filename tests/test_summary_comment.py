"""Tests for the top-level PR summary comment (post once, update on re-run)."""

import json

import pytest
import requests

from reviewer import github_client
from reviewer.config import Config
from reviewer.reviewer import review_pull_request
from reviewer.summary import SUMMARY_MARKER, build_summary_comment

DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,6 +10,7 @@ def get_user(user_id):
     query = "SELECT * FROM users WHERE id = " + str(user_id)
     cursor.execute(query)
+    return cursor.fetchone()
"""

NEW_DIFF = """diff --git a/src/new.py b/src/new.py
index 3333333..4444444 100644
--- a/src/new.py
+++ b/src/new.py
@@ -1,3 +1,4 @@ def helper():
     x = 1
+    return x
"""

FINDING = json.dumps(
    [
        {
            "path": "src/app.py",
            "line": 12,
            "severity": "critical",
            "category": "security",
            "message": "SQL injection.",
        },
        {
            "path": "src/app.py",
            "line": 13,
            "severity": "minor",
            "category": "style",
            "message": "Trailing whitespace.",
        },
    ]
)

NEW_FINDING = json.dumps(
    [
        {
            "path": "src/new.py",
            "line": 3,
            "severity": "major",
            "category": "correctness",
            "message": "Wrong return.",
        }
    ]
)


def test_build_summary_comment_contents():
    cost = {
        "model": "gpt-4o-mini",
        "prompt_tokens": 8412,
        "completion_tokens": 1203,
        "total_tokens": 9615,
        "cost_usd": 0.001984,
    }
    body = build_summary_comment(
        files_reviewed=3,
        findings=[
            {"severity": "critical"},
            {"severity": "minor"},
            {"severity": "minor"},
        ],
        cost=cost,
        dimensions=["correctness", "security", "performance", "style"],
    )
    assert body.startswith(SUMMARY_MARKER)
    assert "Files reviewed: 3" in body
    assert "1 critical" in body
    assert "2 minor" in body
    assert "gpt-4o-mini" in body
    assert "8,412" in body
    assert "correctness, security, performance, style" in body


def test_build_summary_comment_no_findings():
    cost = {
        "model": "gpt-4o-mini",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost_usd": 0.0,
    }
    body = build_summary_comment(
        files_reviewed=1, findings=[], cost=cost, dimensions=["style"]
    )
    assert "Findings: none" in body


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


class MockAPI:
    """Fake GitHub + LLM where issue comments persist across runs."""

    def __init__(self):
        self.commits = [{"sha": "aaa"}, {"sha": "bbb"}]
        self.issue_comments = []
        self.next_id = 100
        self.summary_posts = 0
        self.patches = []

    def fake_get(self, url, **kwargs):
        accept = kwargs.get("headers", {}).get("Accept", "")
        if url.endswith("/pulls/123/commits"):
            return FakeResponse(json_data=self.commits)
        if "/compare/" in url and accept == "application/vnd.github.diff":
            return FakeResponse(text=NEW_DIFF)
        if url.endswith("/pulls/123") and accept == "application/vnd.github.diff":
            return FakeResponse(text=DIFF)
        if url.endswith("/pulls/123"):
            return FakeResponse(
                json_data={"title": "t", "body": "", "head": {"sha": self.commits[-1]["sha"]}}
            )
        if url.endswith("/issues/123/comments"):
            return FakeResponse(
                json_data=[{"id": c["id"], "body": c["body"]} for c in self.issue_comments]
            )
        return FakeResponse(status_code=404, text="not found")

    def fake_post(self, url, **kwargs):
        if url.endswith("/chat/completions"):
            return FakeResponse(json_data={"choices": [{"message": {"content": FINDING}}]})
        if "/reviews" in url:
            return FakeResponse(status_code=201, json_data={"id": 1})
        if url.endswith("/issues/123/comments"):
            self.summary_posts += 1
            comment = {"id": self.next_id, "body": kwargs["json"]["body"]}
            self.next_id += 1
            self.issue_comments.append(comment)
            return FakeResponse(status_code=201, json_data=comment)
        return FakeResponse(status_code=404, text="not found")

    def fake_patch(self, url, **kwargs):
        comment_id = int(url.rsplit("/", 1)[-1])
        self.patches.append((comment_id, kwargs["json"]["body"]))
        for c in self.issue_comments:
            if c["id"] == comment_id:
                c["body"] = kwargs["json"]["body"]
        return FakeResponse(json_data={"id": comment_id})


@pytest.fixture
def mock_api(monkeypatch):
    api = MockAPI()
    monkeypatch.setattr(requests, "get", api.fake_get)
    monkeypatch.setattr(requests, "post", api.fake_post)
    monkeypatch.setattr(requests, "patch", api.fake_patch)
    return api


def test_first_run_posts_one_summary_comment(mock_api, tmp_path):
    cfg = Config(github_token="gh-test", llm_api_key="llm-test")
    result = review_pull_request(
        cfg, "octocat/hello-world", 123, dry_run=False,
        state_path=str(tmp_path / "state.json"),
    )
    assert result["posted"] is True
    assert mock_api.summary_posts == 1
    body = mock_api.issue_comments[0]["body"]
    assert SUMMARY_MARKER in body
    assert "Files reviewed: 1" in body
    assert "1 critical" in body and "1 minor" in body
    assert "LLM cost" not in body  # cost line label differs here
    assert "Estimated cost:" in body
    assert result["summary_comment"] == body


def test_second_run_updates_summary_instead_of_duplicating(mock_api, tmp_path):
    cfg = Config(github_token="gh-test", llm_api_key="llm-test")
    state_path = str(tmp_path / "state.json")
    review_pull_request(cfg, "octocat/hello-world", 123, dry_run=False, state_path=state_path)
    first_id = mock_api.issue_comments[0]["id"]

    # New commit lands; the mock serves the incremental diff for the compare.
    mock_api.commits.append({"sha": "ccc"})
    result = review_pull_request(cfg, "octocat/hello-world", 123, dry_run=False, state_path=state_path)

    assert result["incremental"] is True
    assert mock_api.summary_posts == 1  # no second top-level comment
    assert len(mock_api.issue_comments) == 1
    assert len(mock_api.patches) == 1
    patched_id, patched_body = mock_api.patches[0]
    assert patched_id == first_id
    assert SUMMARY_MARKER in patched_body
    assert "Incremental review:" in patched_body


def test_post_or_update_creates_when_no_marker_match(mock_api):
    mock_api.issue_comments.append({"id": 1, "body": "just a human comment"})
    github_client.post_or_update_summary_comment("t", "o/r", 123, SUMMARY_MARKER + "\nhello")
    assert mock_api.summary_posts == 1
    assert mock_api.patches == []
