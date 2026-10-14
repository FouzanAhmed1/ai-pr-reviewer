"""Tests for CLI arg parsing, config loading, and action.yml wiring."""

import os

import pytest
import yaml

from reviewer.__main__ import build_arg_parser, main
from reviewer.config import Config, ConfigError


def test_cli_parses_repo_pr_and_dry_run():
    args = build_arg_parser().parse_args(["--repo", "octocat/hello-world", "--pr", "123", "--dry-run"])
    assert args.repo == "octocat/hello-world"
    assert args.pr == 123
    assert args.dry_run is True


def test_cli_dry_run_defaults_off():
    args = build_arg_parser().parse_args(["--repo", "o/r", "--pr", "1"])
    assert args.dry_run is False


def test_cli_requires_repo_and_pr():
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--repo", "o/r"])
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--pr", "1"])


def test_cli_pr_must_be_int():
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--repo", "o/r", "--pr", "abc"])


def test_config_from_env_reads_all_vars(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-x")
    monkeypatch.setenv("LLM_API_KEY", "llm-x")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "my-model")
    monkeypatch.setenv("MAX_CHUNK_TOKENS", "1234")
    cfg = Config.from_env()
    assert cfg.github_token == "gh-x"
    assert cfg.llm_api_key == "llm-x"
    assert cfg.llm_base_url == "https://llm.example.com/v1"
    assert cfg.llm_model == "my-model"
    assert cfg.max_chunk_tokens == 1234


def test_config_defaults_when_optional_vars_missing(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-x")
    monkeypatch.setenv("LLM_API_KEY", "llm-x")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("MAX_CHUNK_TOKENS", raising=False)
    cfg = Config.from_env()
    assert cfg.llm_base_url == "https://api.openai.com/v1"
    assert cfg.llm_model == "gpt-4o-mini"
    assert cfg.max_chunk_tokens == 4000


def test_config_rejects_missing_github_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "llm-x")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_config_rejects_missing_llm_key(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-x")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        Config.from_env()


def test_config_rejects_bad_max_tokens(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-x")
    monkeypatch.setenv("LLM_API_KEY", "llm-x")
    monkeypatch.setenv("MAX_CHUNK_TOKENS", "not-a-number")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_main_errors_without_config(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "llm-x")
    with pytest.raises(SystemExit) as exc:
        main(["--repo", "o/r", "--pr", "1"])
    assert exc.value.code != 0


def test_action_yml_is_valid_and_wired():
    action_path = os.path.join(os.path.dirname(__file__), "..", "action.yml")
    with open(action_path) as f:
        action = yaml.safe_load(f)

    assert action["runs"]["using"] == "composite"
    inputs = action["inputs"]
    assert inputs["github-token"]["required"] is True
    assert inputs["llm-api-key"]["required"] is True
    assert inputs["llm-base-url"]["default"] == "https://api.openai.com/v1"
    assert inputs["llm-model"]["default"] == "gpt-4o-mini"

    # Every env var the code reads must be wired from an action input.
    run_step = action["runs"]["steps"][-1]
    env = run_step["env"]
    assert env["GITHUB_TOKEN"] == "${{ inputs.github-token }}"
    assert env["LLM_API_KEY"] == "${{ inputs.llm-api-key }}"
    assert env["LLM_BASE_URL"] == "${{ inputs.llm-base-url }}"
    assert env["LLM_MODEL"] == "${{ inputs.llm-model }}"
    assert env["MAX_CHUNK_TOKENS"] == "${{ inputs.max-chunk-tokens }}"
    # The runner must target the PR from the triggering event.
    assert "github.event.pull_request.number" in run_step["run"]
    assert "github.repository" in run_step["run"]
