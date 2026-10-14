"""Configuration for ai-pr-reviewer, sourced entirely from environment variables.

Nothing secret lives in code. Every setting is read from the environment so
the same code path works as a GitHub Action and as a local CLI.
"""

import os

#: Env var names (documented in .env.example and README.md).
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
LLM_API_KEY_ENV = "LLM_API_KEY"
LLM_BASE_URL_ENV = "LLM_BASE_URL"
LLM_MODEL_ENV = "LLM_MODEL"
MAX_CHUNK_TOKENS_ENV = "MAX_CHUNK_TOKENS"

DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_MAX_CHUNK_TOKENS = 4000

SEVERITIES = ("critical", "major", "minor", "nit")


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class Config:
    """Resolved, validated configuration for a review run."""

    def __init__(
        self,
        github_token,
        llm_api_key,
        llm_base_url=DEFAULT_LLM_BASE_URL,
        llm_model=DEFAULT_LLM_MODEL,
        max_chunk_tokens=DEFAULT_MAX_CHUNK_TOKENS,
    ):
        if not github_token:
            raise ConfigError(f"{GITHUB_TOKEN_ENV} is required but was not set.")
        if not llm_api_key:
            raise ConfigError(f"{LLM_API_KEY_ENV} is required but was not set.")
        if max_chunk_tokens <= 0:
            raise ConfigError(f"{MAX_CHUNK_TOKENS_ENV} must be a positive integer.")

        self.github_token = github_token
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url.rstrip("/")
        self.llm_model = llm_model
        self.max_chunk_tokens = max_chunk_tokens

    @classmethod
    def from_env(cls):
        """Build a Config from the process environment."""
        max_tokens_raw = os.environ.get(MAX_CHUNK_TOKENS_ENV, "")
        try:
            max_tokens = int(max_tokens_raw) if max_tokens_raw else DEFAULT_MAX_CHUNK_TOKENS
        except ValueError:
            raise ConfigError(f"{MAX_CHUNK_TOKENS_ENV} must be an integer, got {max_tokens_raw!r}.")

        return cls(
            github_token=os.environ.get(GITHUB_TOKEN_ENV, ""),
            llm_api_key=os.environ.get(LLM_API_KEY_ENV, ""),
            llm_base_url=os.environ.get(LLM_BASE_URL_ENV, DEFAULT_LLM_BASE_URL) or DEFAULT_LLM_BASE_URL,
            llm_model=os.environ.get(LLM_MODEL_ENV, DEFAULT_LLM_MODEL) or DEFAULT_LLM_MODEL,
            max_chunk_tokens=max_tokens,
        )
