"""Tests for token counting and cost tracking."""

import json
import sys

import pytest
import requests

from reviewer import cost as cost_mod
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
            "message": "SQL injection.",
        }
    ]
)


def hide_tiktoken(monkeypatch):
    # Force the len/4 fallback even if tiktoken is installed.
    monkeypatch.setitem(sys.modules, "tiktoken", None)


def test_count_tokens_falls_back_to_len_over_4(monkeypatch):
    hide_tiktoken(monkeypatch)
    assert cost_mod.count_tokens("a" * 100, "gpt-4o-mini") == 25
    assert cost_mod.count_tokens("", "gpt-4o-mini") == 1  # never zero


def test_resolve_prices_builtin_table():
    assert cost_mod.resolve_prices("gpt-4o-mini") == (0.15, 0.60)
    assert cost_mod.resolve_prices("gpt-4o") == (2.50, 10.00)


def test_resolve_prices_config_override_wins():
    overrides = {"gpt-4o-mini": {"prompt": 1.0, "completion": 2.0}}
    assert cost_mod.resolve_prices("gpt-4o-mini", overrides) == (1.0, 2.0)


def test_resolve_prices_unknown_model_is_zero():
    assert cost_mod.resolve_prices("some-future-model") == (0.0, 0.0)


def test_cost_tracker_math():
    tracker = cost_mod.CostTracker("gpt-4o-mini")
    tracker.record(1_000_000, 500_000)
    # 1M prompt @ $0.15 + 0.5M completion @ $0.60 = $0.45
    assert tracker.cost_usd == pytest.approx(0.45)
    assert tracker.total_tokens == 1_500_000


def test_cost_tracker_respects_pricing_override():
    tracker = cost_mod.CostTracker(
        "my-model", {"my-model": {"prompt": 1.0, "completion": 2.0}}
    )
    tracker.record(1_000_000, 1_000_000)
    assert tracker.cost_usd == pytest.approx(3.0)


def test_summary_line_contains_totals():
    tracker = cost_mod.CostTracker("gpt-4o-mini")
    tracker.record(8412, 1203)
    line = tracker.summary_line()
    assert "8,412" in line
    assert "1,203" in line
    assert "gpt-4o-mini" in line
    assert line.startswith("LLM cost: ~$")


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


def test_run_accumulates_cost_with_fake_counter(monkeypatch, tmp_path):
    monkeypatch.setattr(cost_mod, "count_tokens", lambda text, model: len(text) // 2)

    def fake_get(url, **kwargs):
        if url.endswith("/pulls/123/commits"):
            return FakeResponse(json_data=[{"sha": "abc123"}])
        if kwargs.get("headers", {}).get("Accept") == "application/vnd.github.diff":
            return FakeResponse(text=DIFF)
        return FakeResponse(
            json_data={"title": "t", "body": "", "head": {"sha": "abc123"}}
        )

    def fake_post(url, **kwargs):
        if url.endswith("/chat/completions"):
            return FakeResponse(
                json_data={"choices": [{"message": {"content": LLM_FINDING}}]}
            )
        return FakeResponse(status_code=201, json_data={"id": 1})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    cfg = Config(github_token="gh-test", llm_api_key="llm-test")
    result = review_pull_request(
        cfg, "octocat/hello-world", 123, dry_run=True,
        state_path=str(tmp_path / "state.json"),
    )

    cost = result["cost"]
    assert cost["model"] == "gpt-4o-mini"
    assert cost["prompt_tokens"] > 0
    assert cost["completion_tokens"] > 0
    assert cost["total_tokens"] == cost["prompt_tokens"] + cost["completion_tokens"]
    # gpt-4o-mini pricing applied: cost > 0 for nonzero tokens.
    assert cost["cost_usd"] > 0
    # The run summary carries the cost line.
    assert "LLM cost: ~$" in result["summary"]
    assert result["cost_line"] in result["summary"]
