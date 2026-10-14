# ai-pr-reviewer

A GitHub Action and CLI that reviews pull request diffs with an LLM and posts
inline review comments back on the PR. It works with any OpenAI-compatible chat
completions endpoint (OpenAI, OpenRouter, Ollama, vLLM, or your own gateway),
so you are not locked into one provider.

## Architecture

Four stages, each a small module in `reviewer/`:

1. **Fetch** (`github_client.py`): pulls the PR title/body and the unified diff
   via the GitHub REST API using `GITHUB_TOKEN`.
2. **Chunk** (`chunker.py`): splits the diff into per-file, then per-hunk
   pieces that fit a token budget (estimated as characters / 4). A single
   oversized hunk is split by lines as a last resort, so no chunk ever exceeds
   `MAX_CHUNK_TOKENS` by a meaningful amount.
3. **Review** (`llm.py`, `parser.py`): sends each chunk to the LLM with a
   system prompt that demands strict JSON: a list of
   `{path, line, severity, category, message}` findings covering correctness,
   security, performance, and code style. Malformed JSON is retried once, then
   the chunk is skipped with a warning instead of failing the run.
4. **Post** (`github_client.py`): submits one inline review on the PR with each
   finding as a comment on its file and line, plus a summary with severity
   counts.

The GitHub Action (`action.yml`) is a composite action that installs the
dependencies and runs `python -m reviewer` against the PR that triggered the
workflow. The CLI (`reviewer/__main__.py`) runs the same pipeline locally.

## Setup on a fresh machine

```bash
git clone <this-repo>   # or copy the directory
cd ai-pr-reviewer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then fill in your values
```

Requirements: Python 3.10 or newer.

## Usage

### As a GitHub Action

Add `.github/workflows/ai-review.yml` to your repo:

```yaml
name: AI PR review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - uses: your-org/ai-pr-reviewer@v1
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          llm-api-key: ${{ secrets.LLM_API_KEY }}
          # llm-base-url: https://api.openai.com/v1   # optional
          # llm-model: gpt-4o-mini                   # optional
```

### As a CLI

```bash
export GITHUB_TOKEN=ghp_...
export LLM_API_KEY=sk_...

# Print the review locally without posting anything:
python -m reviewer --repo octocat/hello-world --pr 123 --dry-run

# JSON output for scripting:
python -m reviewer --repo octocat/hello-world --pr 123 --dry-run --json

# Actually post the review on the PR:
python -m reviewer --repo octocat/hello-world --pr 123

# Override the chunk budget for this run:
python -m reviewer --repo octocat/hello-world --pr 123 --dry-run --max-chunk-tokens 8000
```

## Config reference

Everything is configured through environment variables. No secrets in code.

| Variable | Required | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | yes | | Token used to fetch the diff and post the review. In Actions, use `${{ secrets.GITHUB_TOKEN }}` with `pull-requests: write` permission. |
| `LLM_API_KEY` | yes | | API key for the OpenAI-compatible LLM endpoint. |
| `LLM_BASE_URL` | no | `https://api.openai.com/v1` | Base URL of the chat completions endpoint. Point it at OpenRouter, Ollama, vLLM, etc. |
| `LLM_MODEL` | no | `gpt-4o-mini` | Model name sent to the endpoint. |
| `MAX_CHUNK_TOKENS` | no | `4000` | Max estimated tokens per diff chunk. Lower it for small-context models. |

The Action exposes the same settings as inputs: `github-token`, `llm-api-key`,
`llm-base-url`, `llm-model`, `max-chunk-tokens`, plus `dry-run`.

## Sample review output

For a diff like this:

```diff
@@ -10,6 +10,7 @@ def get_user(user_id):
     query = "SELECT * FROM users WHERE id = " + str(user_id)
     cursor.execute(query)
+    return cursor.fetchone()
```

the reviewer posts an inline comment on `src/app.py`, line 12:

> **[CRITICAL]** (security)
>
> SQL injection: the query is built by string concatenation with `user_id`.
> Use a parameterized query (`cursor.execute("... WHERE id = %s", (user_id,))`)
> so untrusted input can never alter the SQL.

The review summary posted above the comments looks like this:

> AI review complete: 1 finding(s) (1 critical).
> Note: 2 diff chunk(s) could not be parsed and were skipped.

Severity labels used: `critical`, `major`, `minor`, `nit`. Every posted comment
includes its severity label so you can scan a review in seconds.

## Development

```bash
pip install -r requirements.txt
python -m pytest tests/ -q
```

Tests are fully offline: the GitHub API and the LLM endpoint are mocked, so the
suite runs without credentials or network access.
