"""Incremental review tests: commit listing + state, fully mocked HTTP."""

import json

import pytest
import requests

from reviewer import state as state_mod
from reviewer.config import Config
from reviewer.reviewer import review_pull_request

FULL_DIFF = """diff --git a/src/app.py b/src/app.py
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

LLM_FINDING = json.dumps(
    [
        {
            "path": "src/app.py",
            "line": 12,
            "severity": "major",
            "category": "security",
            "message": "SQL injection via string concatenation.",
        }
    ]
)

LLM_NEW_FINDING = json.dumps(
    [
        {
            "path": "src/new.py",
            "line": 3,
            "severity": "minor",
            "category": "style",
            "message": "Single-letter variable name.",
        }
    ]
)


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


class MockGitHub:
    """Fake GitHub + LLM endpoints with a mutable commit list."""

    def __init__(self):
        self.commits = [{"sha": "aaa111"}, {"sha": "bbb222"}]
        self.calls = {"full_diff": 0, "compare": [], "llm": 0}

    def fake_get(self, url, **kwargs):
        accept = kwargs.get("headers", {}).get("Accept", "")
        if url.endswith("/pulls/123/commits"):
            return FakeResponse(json_data=self.commits)
        if "/compare/" in url and accept == "application/vnd.github.diff":
            self.calls["compare"].append(url)
            return FakeResponse(text=NEW_DIFF)
        if url.endswith("/pulls/123") and accept == "application/vnd.github.diff":
            self.calls["full_diff"] += 1
            return FakeResponse(text=FULL_DIFF)
        if url.endswith("/pulls/123"):
            return FakeResponse(
                json_data={"title": "t", "body": "", "head": {"sha": self.commits[-1]["sha"]}}
            )
        return FakeResponse(status_code=404, text="not found")

    def fake_post(self, url, **kwargs):
        if url.endswith("/chat/completions"):
            self.calls["llm"] += 1
            content = LLM_NEW_FINDING if self.calls["compare"] else LLM_FINDING
            return FakeResponse(json_data={"choices": [{"message": {"content": content}}]})
        return FakeResponse(status_code=201, json_data={"id": 1})


@pytest.fixture
def mock_github(monkeypatch):
    mock = MockGitHub()
    monkeypatch.setattr(requests, "get", mock.fake_get)
    monkeypatch.setattr(requests, "post", mock.fake_post)
    return mock


def make_config():
    return Config(github_token="gh-test", llm_api_key="llm-test")


def test_second_run_reviews_only_new_commits(mock_github, tmp_path):
    state_path = str(tmp_path / "state.json")

    # First run: full review of commits aaa111, bbb222.
    result1 = review_pull_request(
        make_config(), "octocat/hello-world", 123, dry_run=True, state_path=state_path
    )
    assert result1["incremental"] is False
    assert mock_github.calls["full_diff"] == 1
    assert mock_github.calls["compare"] == []
    assert result1["findings"][0]["path"] == "src/app.py"

    stored = state_mod.load_state(state_path)
    assert state_mod.get_last_reviewed_sha(stored, "octocat/hello-world", 123) == "bbb222"

    # A new commit lands; second run must only review the new diff.
    mock_github.commits.append({"sha": "ccc333"})
    result2 = review_pull_request(
        make_config(), "octocat/hello-world", 123, dry_run=True, state_path=state_path
    )
    assert result2["incremental"] is True
    assert result2["commits_reviewed"] == 1
    assert mock_github.calls["full_diff"] == 1  # no second full fetch
    assert len(mock_github.calls["compare"]) == 1
    assert "bbb222...ccc333" in mock_github.calls["compare"][0]
    # Only the new commit's finding was reviewed.
    assert [f["path"] for f in result2["findings"]] == ["src/new.py"]

    stored = state_mod.load_state(state_path)
    assert state_mod.get_last_reviewed_sha(stored, "octocat/hello-world", 123) == "ccc333"


def test_no_new_commits_returns_up_to_date(mock_github, tmp_path):
    state_path = str(tmp_path / "state.json")
    review_pull_request(make_config(), "octocat/hello-world", 123, dry_run=True, state_path=state_path)

    llm_calls_before = mock_github.calls["llm"]
    result = review_pull_request(
        make_config(), "octocat/hello-world", 123, dry_run=True, state_path=state_path
    )
    assert result["up_to_date"] is True
    assert result["findings"] == []
    assert mock_github.calls["llm"] == llm_calls_before  # no LLM calls at all


def test_full_flag_ignores_state(mock_github, tmp_path):
    state_path = str(tmp_path / "state.json")
    review_pull_request(make_config(), "octocat/hello-world", 123, dry_run=True, state_path=state_path)

    result = review_pull_request(
        make_config(), "octocat/hello-world", 123, dry_run=True,
        state_path=state_path, full=True,
    )
    assert result["incremental"] is False
    assert mock_github.calls["full_diff"] == 2  # full diff fetched again
    assert mock_github.calls["compare"] == []
    assert [f["path"] for f in result["findings"]] == ["src/app.py"]


def test_compare_failure_falls_back_to_full_diff(monkeypatch, tmp_path):
    mock = MockGitHub()
    monkeypatch.setattr(requests, "get", mock.fake_get)
    monkeypatch.setattr(requests, "post", mock.fake_post)
    state_path = str(tmp_path / "state.json")
    review_pull_request(make_config(), "octocat/hello-world", 123, dry_run=True, state_path=state_path)

    def fake_get_compare_fails(url, **kwargs):
        if "/compare/" in url:
            return FakeResponse(status_code=404, text="no such compare")
        return mock.fake_get(url, **kwargs)

    mock.commits.append({"sha": "ccc333"})
    monkeypatch.setattr(requests, "get", fake_get_compare_fails)

    result = review_pull_request(
        make_config(), "octocat/hello-world", 123, dry_run=True, state_path=state_path
    )
    assert result["incremental"] is False  # fell back to full diff
    assert [f["path"] for f in result["findings"]] == ["src/app.py"]
