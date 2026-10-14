"""Unit tests for the incremental-review state file."""

import json

from reviewer import state as state_mod


def test_state_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    state = state_mod.load_state(path)
    assert state == {}

    state_mod.set_last_reviewed_sha(state, "octocat/hello-world", 123, "abc123")
    state_mod.save_state(path, state)

    reloaded = state_mod.load_state(path)
    assert state_mod.get_last_reviewed_sha(reloaded, "octocat/hello-world", 123) == "abc123"


def test_state_keyed_by_repo_and_pr(tmp_path):
    path = str(tmp_path / "state.json")
    state = {}
    state_mod.set_last_reviewed_sha(state, "octocat/hello-world", 123, "sha-1")
    state_mod.set_last_reviewed_sha(state, "octocat/hello-world", 124, "sha-2")
    state_mod.set_last_reviewed_sha(state, "other/repo", 123, "sha-3")
    state_mod.save_state(path, state)

    reloaded = state_mod.load_state(path)
    assert state_mod.get_last_reviewed_sha(reloaded, "octocat/hello-world", 123) == "sha-1"
    assert state_mod.get_last_reviewed_sha(reloaded, "octocat/hello-world", 124) == "sha-2"
    assert state_mod.get_last_reviewed_sha(reloaded, "other/repo", 123) == "sha-3"
    assert state_mod.get_last_reviewed_sha(reloaded, "octocat/hello-world", 999) is None


def test_state_missing_file_returns_empty(tmp_path):
    assert state_mod.load_state(str(tmp_path / "nope.json")) == {}


def test_state_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not valid json")
    assert state_mod.load_state(str(path)) == {}


def test_state_non_object_file_returns_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(["a", "list"]))
    assert state_mod.load_state(str(path)) == {}


def test_default_state_path_name():
    assert state_mod.DEFAULT_STATE_PATH == ".pr-reviewer-state.json"
