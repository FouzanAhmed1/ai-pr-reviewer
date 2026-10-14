"""Tests for .pr-reviewer.yaml loading, validation, and behavior."""

import json
import os

import pytest
import requests

from reviewer import llm
from reviewer.__main__ import build_arg_parser
from reviewer.chunker import chunk_diff
from reviewer.config import Config, ConfigError
from reviewer.config_file import load_config_file, resolve_config_path
from reviewer.reviewer import (
    apply_finding_filters,
    is_ignored,
    language_for_path,
    make_budget_for,
    review_pull_request,
)

VALID_YAML = """\
max_chunk_tokens: 2000
review_rules:
  correctness: true
  security: true
  performance: false
  style: true
ignore_paths:
  - "*.lock"
  - "dist/**"
severity_threshold: major
per_language:
  python:
    max_chunk_tokens: 4000
pricing:
  gpt-4o-mini:
    prompt: 0.15
    completion: 0.60
"""

DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,6 +10,7 @@ def get_user(user_id):
     query = "SELECT * FROM users WHERE id = " + str(user_id)
     cursor.execute(query)
+    return cursor.fetchone()
diff --git a/dist/bundle.js b/dist/bundle.js
index aaaaaaa..bbbbbbb 100644
--- a/dist/bundle.js
+++ b/dist/bundle.js
@@ -1 +1 @@
-var x=1
+var x=2
diff --git a/package.lock b/package.lock
index ccccccc..ddddddd 100644
--- a/package.lock
+++ b/package.lock
@@ -1 +1 @@
-{"v":1}
+{"v":2}
"""


def write_config(tmp_path, text):
    path = tmp_path / ".pr-reviewer.yaml"
    path.write_text(text)
    return str(path)


def env_with_tokens(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test")
    monkeypatch.setenv("LLM_API_KEY", "llm-test")
    monkeypatch.delenv("MAX_CHUNK_TOKENS", raising=False)


# --- parsing ---


def test_load_valid_config(tmp_path):
    data = load_config_file(write_config(tmp_path, VALID_YAML))
    assert data["max_chunk_tokens"] == 2000
    assert data["review_rules"] == {
        "correctness": True,
        "security": True,
        "performance": False,
        "style": True,
    }
    assert data["ignore_paths"] == ["*.lock", "dist/**"]
    assert data["severity_threshold"] == "major"
    assert data["per_language"] == {"python": {"max_chunk_tokens": 4000}}
    assert data["pricing"] == {"gpt-4o-mini": {"prompt": 0.15, "completion": 0.60}}


def test_load_empty_config(tmp_path):
    assert load_config_file(write_config(tmp_path, "")) == {}


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config_file(str(tmp_path / "nope.yaml"))


@pytest.mark.parametrize(
    "text, match",
    [
        ("severity_threshold: bogus\n", "severity_threshold"),
        ("review_rules:\n  hacking: true\n", "unknown dimension"),
        ("review_rules:\n  security: yes-please\n", "must be true or false"),
        ("review_rules: [1, 2]\n", "must be a mapping"),
        ("ignore_paths: dist/**\n", "must be a list of glob strings"),
        ("per_language:\n  python:\n    max_chunk_tokens: 0\n", "positive integer"),
        ("per_language:\n  python:\n    bogus_opt: 1\n", "unknown option"),
        ("pricing:\n  m:\n    prompt: 1.0\n", "missing 'completion'"),
        ("pricing:\n  m:\n    prompt: -1\n    completion: 2\n", "must not be negative"),
        ("max_chunk_tokens: -5\n", "positive integer"),
        ("totally_unknown_key: 1\n", "unknown key"),
        ("- just\n- a\n- list\n", "must contain a YAML mapping"),
    ],
)
def test_load_invalid_config_raises_clear_error(tmp_path, text, match):
    with pytest.raises(ConfigError, match=match):
        load_config_file(write_config(tmp_path, text))


# --- resolution ---


def test_resolve_explicit_path(tmp_path):
    path = str(tmp_path / "custom.yaml")
    assert resolve_config_path(path) == path


def test_resolve_auto_loads_from_cwd(tmp_path, monkeypatch):
    write_config(tmp_path, "severity_threshold: minor\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    assert resolve_config_path(None) == str(tmp_path / ".pr-reviewer.yaml")


def test_resolve_prefers_github_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".pr-reviewer.yaml").write_text("severity_threshold: minor\n")
    write_config(tmp_path, "severity_threshold: major\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(ws))
    assert resolve_config_path(None) == str(ws / ".pr-reviewer.yaml")


def test_resolve_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_WORKSPACE", raising=False)
    assert resolve_config_path(None) is None


# --- merge precedence ---


def test_config_merges_over_defaults(tmp_path, monkeypatch):
    env_with_tokens(monkeypatch)
    cfg = Config.from_env_and_file(write_config(tmp_path, VALID_YAML))
    assert cfg.max_chunk_tokens == 2000
    assert cfg.review_rules["performance"] is False
    assert cfg.ignore_paths == ["*.lock", "dist/**"]
    assert cfg.severity_threshold == "major"
    assert cfg.per_language == {"python": {"max_chunk_tokens": 4000}}


def test_env_overrides_config_file(tmp_path, monkeypatch):
    env_with_tokens(monkeypatch)
    monkeypatch.setenv("MAX_CHUNK_TOKENS", "777")
    cfg = Config.from_env_and_file(write_config(tmp_path, "max_chunk_tokens: 2000\n"))
    assert cfg.max_chunk_tokens == 777


def test_no_config_file_uses_defaults(monkeypatch):
    env_with_tokens(monkeypatch)
    cfg = Config(github_token="t", llm_api_key="k")
    assert cfg.review_rules == {
        "correctness": True,
        "security": True,
        "performance": True,
        "style": True,
    }
    assert cfg.ignore_paths == []
    assert cfg.severity_threshold is None
    assert cfg.per_language == {}


def test_cli_accepts_config_flag():
    args = build_arg_parser().parse_args(
        ["--repo", "o/r", "--pr", "1", "--config", "my.yaml"]
    )
    assert args.config == "my.yaml"


# --- behavior ---


def test_is_ignored_globs():
    assert is_ignored("dist/bundle.js", ["dist/**"])
    assert is_ignored("package.lock", ["*.lock"])
    assert is_ignored("src/package.lock", ["*.lock"])
    assert not is_ignored("src/app.py", ["*.lock", "dist/**"])
    assert not is_ignored("src/app.py", [])


def test_language_for_path():
    assert language_for_path("src/app.py") == "python"
    assert language_for_path("web/app.TSX") == "typescript"
    assert language_for_path("main.go") == "go"
    assert language_for_path("README.md") is None


def test_make_budget_for_applies_per_language():
    cfg = Config(
        github_token="t",
        llm_api_key="k",
        max_chunk_tokens=1000,
        per_language={"python": {"max_chunk_tokens": 4000}},
    )
    budget_for = make_budget_for(cfg)
    assert budget_for("src/app.py") == 4000
    assert budget_for("src/app.js") == 1000


def test_chunk_diff_uses_per_file_budget():
    big_py = "diff --git a/a.py b/a.py\n" + ("+x = 1\n" * 200)
    budget_for = lambda path: 100 if path.endswith(".py") else 10**9
    chunks = chunk_diff(big_py, 10**9, budget_for)
    assert len(chunks) > 1
    # Without the override it is a single chunk.
    assert len(chunk_diff(big_py, 10**9)) == 1


def test_severity_threshold_filters_findings():
    cfg = Config(
        github_token="t", llm_api_key="k", severity_threshold="major"
    )
    findings = [
        {"severity": "critical", "category": "security"},
        {"severity": "major", "category": "correctness"},
        {"severity": "minor", "category": "style"},
        {"severity": "nit", "category": "style"},
    ]
    kept = apply_finding_filters(findings, cfg)
    assert [f["severity"] for f in kept] == ["critical", "major"]


def test_disabled_dimension_filters_findings():
    cfg = Config(
        github_token="t", llm_api_key="k", review_rules={"security": False}
    )
    findings = [
        {"severity": "major", "category": "security"},
        {"severity": "minor", "category": "style"},
    ]
    kept = apply_finding_filters(findings, cfg)
    assert [f["category"] for f in kept] == ["style"]


def test_system_prompt_omits_disabled_dimensions():
    prompt = llm.build_system_prompt(["correctness", "style"])
    assert "injection" not in prompt
    assert "Correctness" in prompt
    assert "Code style" in prompt


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text)


def test_reviewer_applies_ignore_paths_and_threshold(monkeypatch, tmp_path):
    env_with_tokens(monkeypatch)
    config_path = write_config(
        tmp_path,
        "ignore_paths:\n  - 'dist/**'\n  - '*.lock'\nseverity_threshold: major\n",
    )
    cfg = Config.from_env_and_file(config_path)

    llm_calls = []

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
            llm_calls.append(kwargs["json"]["messages"])
            # One critical (kept) and one nit (dropped by threshold).
            content = json.dumps(
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
                        "severity": "nit",
                        "category": "style",
                        "message": "Trailing whitespace.",
                    },
                ]
            )
            return FakeResponse(json_data={"choices": [{"message": {"content": content}}]})
        return FakeResponse(status_code=201, json_data={"id": 1})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    result = review_pull_request(
        cfg, "octocat/hello-world", 123, dry_run=True,
        state_path=str(tmp_path / "state.json"),
    )
    # Only src/app.py was sent to the LLM; dist/ and *.lock were ignored.
    assert len(llm_calls) == 1
    assert "dist/bundle.js" not in llm_calls[0][1]["content"]
    assert "package.lock" not in llm_calls[0][1]["content"]
    # Threshold dropped the nit finding.
    assert [f["severity"] for f in result["findings"]] == ["critical"]
    assert len(result["comments"]) == 1
