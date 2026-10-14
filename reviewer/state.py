"""Persist incremental review state: which commit SHAs have been reviewed.

The state file maps "<repo>#<pr>" to {"last_reviewed_sha": sha}. On a re-run
the reviewer lists the PR's commits, diffs only the commits pushed after the
recorded SHA, and updates the state afterwards. Pass --full to force a full
re-review. A missing or corrupt state file is treated as "never reviewed".
"""

import json
import logging
import os

log = logging.getLogger(__name__)

#: Default state file location (relative to the working directory).
DEFAULT_STATE_PATH = ".pr-reviewer-state.json"


def _key(repo, pr_number):
    return f"{repo}#{pr_number}"


def load_state(path):
    """Load the state dict from ``path``; return {} when unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read state file %s (%s); starting fresh.", path, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("State file %s does not contain an object; starting fresh.", path)
        return {}
    return data


def save_state(path, state):
    """Write the state dict to ``path`` atomically."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, path)


def get_last_reviewed_sha(state, repo, pr_number):
    """Return the recorded head SHA for this repo+PR, or None."""
    entry = state.get(_key(repo, pr_number))
    if isinstance(entry, dict):
        sha = entry.get("last_reviewed_sha")
        if isinstance(sha, str) and sha:
            return sha
    return None


def set_last_reviewed_sha(state, repo, pr_number, sha):
    """Record ``sha`` as the reviewed head for this repo+PR."""
    state[_key(repo, pr_number)] = {"last_reviewed_sha": sha}
