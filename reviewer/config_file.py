"""Optional YAML config file for ai-pr-reviewer.

``.pr-reviewer.yaml`` is auto-loaded from the repo root when present; ``--config
PATH`` points at a specific file instead. The file merges over the built-in
defaults, and explicit environment variables / CLI flags still win over the
file for the settings they cover.

Schema:
    max_chunk_tokens: 4000        # optional, positive int
    review_rules:                 # optional, enable/disable review dimensions
      correctness: true
      security: true
      performance: false
      style: true
    ignore_paths:                 # optional, glob list, e.g. ["*.lock", "dist/**"]
      - "*.lock"
      - "dist/**"
    severity_threshold: major     # optional: only post findings this severe or worse
    per_language:                 # optional, per-language overrides
      python:
        max_chunk_tokens: 4000
    pricing:                      # optional, USD per 1M tokens overrides
      gpt-4o-mini:
        prompt: 0.15
        completion: 0.60

Anything that does not match the schema raises ConfigError with a message
that names the offending key.
"""

import os

import yaml

from .config import SEVERITIES, ConfigError
from .parser import CATEGORIES

CONFIG_FILENAME = ".pr-reviewer.yaml"


def resolve_config_path(explicit_path=None):
    """Return the config file path to use, or None when there is none.

    Precedence: explicit ``--config`` path, then ``$GITHUB_WORKSPACE`` (the
    checked-out repo when running as an Action), then the working directory.
    """
    if explicit_path:
        return explicit_path
    workspace = os.environ.get("GITHUB_WORKSPACE", "")
    if workspace:
        candidate = os.path.join(workspace, CONFIG_FILENAME)
        if os.path.isfile(candidate):
            return candidate
    candidate = os.path.join(os.getcwd(), CONFIG_FILENAME)
    if os.path.isfile(candidate):
        return candidate
    return None


def load_config_file(path):
    """Load and validate the YAML config at ``path``.

    Returns a plain dict of the validated settings (may be empty). Raises
    ConfigError with a clear message when the file is missing, unparseable,
    or does not match the schema.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}")
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file {path} is not valid YAML: {exc}")

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file {path} must contain a YAML mapping, got {type(data).__name__}."
        )

    known_keys = {
        "max_chunk_tokens",
        "review_rules",
        "ignore_paths",
        "severity_threshold",
        "per_language",
        "pricing",
    }
    for key in data:
        if key not in known_keys:
            raise ConfigError(
                f"Config file {path}: unknown key {key!r}. "
                f"Known keys: {sorted(known_keys)}."
            )

    validated = {}
    if "max_chunk_tokens" in data:
        validated["max_chunk_tokens"] = _positive_int(
            path, "max_chunk_tokens", data["max_chunk_tokens"]
        )
    if "review_rules" in data:
        validated["review_rules"] = _validate_review_rules(path, data["review_rules"])
    if "ignore_paths" in data:
        validated["ignore_paths"] = _validate_ignore_paths(path, data["ignore_paths"])
    if "severity_threshold" in data:
        validated["severity_threshold"] = _validate_severity_threshold(
            path, data["severity_threshold"]
        )
    if "per_language" in data:
        validated["per_language"] = _validate_per_language(path, data["per_language"])
    if "pricing" in data:
        validated["pricing"] = _validate_pricing(path, data["pricing"])
    return validated


def _positive_int(path, key, value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(
            f"Config file {path}: {key!r} must be a positive integer, got {value!r}."
        )
    return value


def _validate_review_rules(path, value):
    where = f"Config file {path}: 'review_rules'"
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a mapping, got {value!r}.")
    rules = {}
    for dim, enabled in value.items():
        if dim not in CATEGORIES:
            raise ConfigError(
                f"{where}: unknown dimension {dim!r}. Known: {list(CATEGORIES)}."
            )
        if not isinstance(enabled, bool):
            raise ConfigError(f"{where}: {dim!r} must be true or false, got {enabled!r}.")
        rules[dim] = enabled
    return rules


def _validate_ignore_paths(path, value):
    where = f"Config file {path}: 'ignore_paths'"
    if not isinstance(value, list) or any(not isinstance(p, str) for p in value):
        raise ConfigError(f"{where} must be a list of glob strings, got {value!r}.")
    return list(value)


def _validate_severity_threshold(path, value):
    if value not in SEVERITIES:
        raise ConfigError(
            f"Config file {path}: 'severity_threshold' must be one of "
            f"{list(SEVERITIES)}, got {value!r}."
        )
    return value


def _validate_per_language(path, value):
    where = f"Config file {path}: 'per_language'"
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a mapping, got {value!r}.")
    result = {}
    for lang, opts in value.items():
        if not isinstance(opts, dict):
            raise ConfigError(f"{where}: {lang!r} must be a mapping, got {opts!r}.")
        lang_opts = {}
        for opt_key, opt_value in opts.items():
            if opt_key != "max_chunk_tokens":
                raise ConfigError(
                    f"{where}: {lang!r} has unknown option {opt_key!r}; "
                    "only 'max_chunk_tokens' is supported."
                )
            lang_opts[opt_key] = _positive_int(
                path, f"per_language.{lang}.max_chunk_tokens", opt_value
            )
        result[str(lang)] = lang_opts
    return result


def _validate_pricing(path, value):
    where = f"Config file {path}: 'pricing'"
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a mapping, got {value!r}.")
    result = {}
    for model, prices in value.items():
        if not isinstance(prices, dict):
            raise ConfigError(f"{where}: {model!r} must be a mapping, got {prices!r}.")
        for price_key in ("prompt", "completion"):
            if price_key not in prices:
                raise ConfigError(
                    f"{where}: {model!r} is missing {price_key!r} (USD per 1M tokens)."
                )
        for price_key, price_value in prices.items():
            if price_key not in ("prompt", "completion"):
                raise ConfigError(
                    f"{where}: {model!r} has unknown price {price_key!r}; "
                    "only 'prompt' and 'completion' are supported."
                )
            if isinstance(price_value, bool) or not isinstance(price_value, (int, float)):
                raise ConfigError(
                    f"{where}: {model!r}.{price_key!r} must be a number, got {price_value!r}."
                )
            if price_value < 0:
                raise ConfigError(
                    f"{where}: {model!r}.{price_key!r} must not be negative."
                )
        result[str(model)] = {
            "prompt": float(prices["prompt"]),
            "completion": float(prices["completion"]),
        }
    return result
