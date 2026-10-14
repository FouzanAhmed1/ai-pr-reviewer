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

#: Severity ordering used by ``severity_threshold``: a finding is kept when
#: its rank is at or above the threshold's rank.
SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1, "nit": 0}

#: Review dimensions, all enabled unless the config file disables them.
DEFAULT_REVIEW_RULES = {
    "correctness": True,
    "security": True,
    "performance": True,
    "style": True,
}


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
        review_rules=None,
        ignore_paths=None,
        severity_threshold=None,
        per_language=None,
        pricing=None,
    ):
        if not github_token:
            raise ConfigError(f"{GITHUB_TOKEN_ENV} is required but was not set.")
        if not llm_api_key:
            raise ConfigError(f"{LLM_API_KEY_ENV} is required but was not set.")
        if max_chunk_tokens <= 0:
            raise ConfigError(f"{MAX_CHUNK_TOKENS_ENV} must be a positive integer.")
        if severity_threshold is not None and severity_threshold not in SEVERITIES:
            raise ConfigError(
                f"severity_threshold must be one of {list(SEVERITIES)}, "
                f"got {severity_threshold!r}."
            )

        self.github_token = github_token
        self.llm_api_key = llm_api_key
        self.llm_base_url = llm_base_url.rstrip("/")
        self.llm_model = llm_model
        self.max_chunk_tokens = max_chunk_tokens
        self.review_rules = {**DEFAULT_REVIEW_RULES, **(review_rules or {})}
        self.ignore_paths = list(ignore_paths or [])
        self.severity_threshold = severity_threshold
        self.per_language = dict(per_language or {})
        self.pricing = dict(pricing or {})

    @classmethod
    def from_env(cls):
        """Build a Config from the process environment (no config file)."""
        return cls.from_env_and_file(explicit_path=None, _skip_autoload=True)

    @classmethod
    def from_env_and_file(cls, explicit_path=None, _skip_autoload=False):
        """Build a Config, merging an optional YAML config file over defaults.

        Precedence: built-in defaults < config file < environment variables.
        ``explicit_path`` is the ``--config`` value; when None and
        ``_skip_autoload`` is false, ``.pr-reviewer.yaml`` is auto-loaded from
        the repo root when present.
        """
        # Deferred import: config_file imports from this module.
        from .config_file import load_config_file, resolve_config_path

        file_data = {}
        if not _skip_autoload or explicit_path:
            config_path = resolve_config_path(explicit_path)
            if config_path:
                file_data = load_config_file(config_path)

        max_tokens_raw = os.environ.get(MAX_CHUNK_TOKENS_ENV, "")
        if max_tokens_raw:
            try:
                max_tokens = int(max_tokens_raw)
            except ValueError:
                raise ConfigError(
                    f"{MAX_CHUNK_TOKENS_ENV} must be an integer, got {max_tokens_raw!r}."
                )
        else:
            max_tokens = file_data.get("max_chunk_tokens", DEFAULT_MAX_CHUNK_TOKENS)

        return cls(
            github_token=os.environ.get(GITHUB_TOKEN_ENV, ""),
            llm_api_key=os.environ.get(LLM_API_KEY_ENV, ""),
            llm_base_url=os.environ.get(LLM_BASE_URL_ENV, DEFAULT_LLM_BASE_URL) or DEFAULT_LLM_BASE_URL,
            llm_model=os.environ.get(LLM_MODEL_ENV, DEFAULT_LLM_MODEL) or DEFAULT_LLM_MODEL,
            max_chunk_tokens=max_tokens,
            review_rules=file_data.get("review_rules"),
            ignore_paths=file_data.get("ignore_paths"),
            severity_threshold=file_data.get("severity_threshold"),
            per_language=file_data.get("per_language"),
            pricing=file_data.get("pricing"),
        )
