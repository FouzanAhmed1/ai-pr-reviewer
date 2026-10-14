"""End-to-end pipeline tests with mocked HTTP (no real network)."""

import json

import pytest
import requests

from reviewer import github_client, llm
from reviewer.config import Config
from reviewer.reviewer import review_pull_request

DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,6 +10,7 @@ def get_user(user_id):
     query = "SELECT * FROM users WHERE id = " + str(user_id)
     cursor.execute(query)
+    return cursor.fetchone()
"""

LLM_FINDING = json.dumps(
    [
        {
            "path": "src/app.py",
            "line": 12,
            "severity": "critical",
            "category": "security",
            "message": "SQL injection: query built by string concatenation. Use parameterized queries.",
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


def make_config():
    return Config(github_token="gh-test", llm_api_key="llm-test")


@pytest.fixture
def mock_http(monkeypatch):
    """Route requests.get/post to canned responses keyed by URL substring."""
    posted = {}

    def fake_get(url, **kwargs):
        if url.endswith("/pulls/123/commits"):
            return FakeResponse(json_data=[{"sha": "abc123"}])
        if "pulls/123" in url and kwargs.get("headers", {}).get("Accept") == "application/vnd.github.diff":
            return FakeResponse(text=DIFF)
        if "pulls/123" in url:
            return FakeResponse(
                json_data={"title": "Add user lookup", "body": "test PR", "head": {"sha": "abc123"}}
            )
        return FakeResponse(status_code=404, text="not found")

    def fake_post(url, **kwargs):
        if url.endswith("/chat/completions"):
            content = LLM_FINDING
            return FakeResponse(
                json_data={"choices": [{"message": {"content": content}}]}
            )
        if "/reviews" in url:
            posted["payload"] = kwargs.get("json")
            posted["url"] = url
            return FakeResponse(status_code=201, json_data={"id": 1})
        return FakeResponse(status_code=404, text="not found")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    return posted


def test_full_pipeline_posts_inline_review(mock_http, tmp_path):
    state_path = str(tmp_path / "state.json")
    result = review_pull_request(make_config(), "octocat/hello-world", 123, dry_run=False, state_path=state_path)
    assert result["posted"] is True
    assert len(result["findings"]) == 1
    assert len(result["comments"]) == 1

    comment = result["comments"][0]
    assert comment["path"] == "src/app.py"
    assert comment["line"] == 12
    assert "CRITICAL" in comment["body"]
    assert "SQL injection" in comment["body"]

    payload = mock_http["payload"]
    assert payload["event"] == "COMMENT"
    assert payload["comments"][0]["path"] == "src/app.py"
    assert payload["comments"][0]["line"] == 12
    assert "octocat/hello-world/pulls/123/reviews" in mock_http["url"]


def test_dry_run_does_not_post(mock_http, tmp_path):
    state_path = str(tmp_path / "state.json")
    result = review_pull_request(make_config(), "octocat/hello-world", 123, dry_run=True, state_path=state_path)
    assert result["posted"] is False
    assert "payload" not in mock_http  # no review POST happened
    assert len(result["comments"]) == 1


def test_malformed_llm_json_retried_once_then_skipped(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        if url.endswith("/pulls/123/commits"):
            return FakeResponse(json_data=[{"sha": "x"}])
        if kwargs.get("headers", {}).get("Accept") == "application/vnd.github.diff":
            return FakeResponse(text=DIFF)
        return FakeResponse(
            json_data={"title": "t", "body": "", "head": {"sha": "x"}}
        )

    def fake_post(url, **kwargs):
        if url.endswith("/chat/completions"):
            calls["n"] += 1
            return FakeResponse(
                json_data={"choices": [{"message": {"content": "garbage {{{"}}]}
            )
        return FakeResponse(status_code=201, json_data={"id": 1})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    state_path = str(tmp_path / "state.json")
    result = review_pull_request(make_config(), "octocat/hello-world", 123, dry_run=True, state_path=state_path)
    assert calls["n"] == 2  # one retry, then give up
    assert result["findings"] == []
    assert result["skipped_chunks"] == 1


def test_empty_diff_returns_early(monkeypatch, tmp_path):
    def fake_get(url, **kwargs):
        if url.endswith("/pulls/123/commits"):
            return FakeResponse(json_data=[{"sha": "x"}])
        if kwargs.get("headers", {}).get("Accept") == "application/vnd.github.diff":
            return FakeResponse(text="")
        return FakeResponse(
            json_data={"title": "t", "body": "", "head": {"sha": "x"}}
        )

    posted = []

    def fake_post(url, **kwargs):
        posted.append(url)
        return FakeResponse(status_code=201, json_data={"id": 1})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    state_path = str(tmp_path / "state.json")
    result = review_pull_request(make_config(), "octocat/hello-world", 123, dry_run=False, state_path=state_path)
    assert result["findings"] == []
    assert result["posted"] is False
    assert posted == []


def test_github_api_error_raises(monkeypatch):
    def fake_get(url, **kwargs):
        return FakeResponse(status_code=401, text="Bad credentials")

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(github_client.GitHubError):
        github_client.get_diff("bad-token", "octocat/hello-world", 123)


def test_llm_error_raises(monkeypatch):
    def fake_post(url, **kwargs):
        return FakeResponse(status_code=500, text="server exploded")

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(llm.LLMError):
        llm.review_chunk("https://x.test/v1", "k", "m", {"title": "t"}, "diff")
