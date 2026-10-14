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

# Only review commits pushed since the last run (see Incremental reviews):
python -m reviewer --repo octocat/hello-world --pr 123 --dry-run

# Force a full re-review, ignoring incremental state:
python -m reviewer --repo octocat/hello-world --pr 123 --dry-run --full

# Use a specific config file instead of auto-loading .pr-reviewer.yaml:
python -m reviewer --repo octocat/hello-world --pr 123 --dry-run --config ./reviewer-config.yaml
```

## Incremental reviews

Re-running the reviewer on a PR only looks at the new commits. Reviewed
commit SHAs are recorded in `.pr-reviewer-state.json` in the working
directory (keyed by repo and PR number); the next run lists the PR commits
and diffs just the ones pushed after the recorded SHA via the GitHub compare
API. Use `--state-path PATH` to keep the state file somewhere else.

A few things worth knowing:

- The state file is updated whenever a run completes, dry runs included, so
  a later real run skips commits you already previewed locally.
- If the recorded SHA is no longer in the PR (say after a force-push), the
  reviewer falls back to a full diff instead of failing.
- When there is nothing new, the run exits early with an "already up to date"
  message and makes no LLM calls.
- In the GitHub Action the state file lives on the ephemeral runner, so
  incremental runs there need the file cached between runs (for example with
  `actions/cache`). Locally it just works.

## Config file

Drop a `.pr-reviewer.yaml` in your repo root and it is picked up
automatically (`$GITHUB_WORKSPACE` is checked first when running as an
Action). Point at a different file with `--config PATH`.

```yaml
# Review only the dimensions you care about (all on by default)
review_rules:
  correctness: true
  security: true
  performance: false
  style: true

# Never send these files to the LLM
ignore_paths:
  - "*.lock"
  - "dist/**"

# Only post findings this severe or worse: critical > major > minor > nit
severity_threshold: major

# Give Python files a bigger chunk budget than the default
per_language:
  python:
    max_chunk_tokens: 4000

# Override prices (USD per 1M tokens) for cost estimates
pricing:
  my-custom-model:
    prompt: 1.00
    completion: 2.00
```

Precedence is: built-in defaults, then the config file, then environment
variables, then CLI flags. A config file that does not match the schema fails
fast with an error naming the offending key, so typos never silently do
nothing.

## Cost tracking

Every run counts prompt and completion tokens for each LLM call and
multiplies by a per-model price table (USD per 1M tokens). Token counts use
tiktoken when it is installed, otherwise a documented characters/4 estimate,
the same rough heuristic the chunker uses for budgeting. The run summary ends
with a cost line:

```
LLM cost: ~$0.0042 (8,412 prompt + 1,203 completion tokens, gpt-4o-mini)
```

Built-in prices cover common OpenAI models; add or reprice any model with the
`pricing:` section of the config file. Models with no price data report $0
and log a warning. For exact counts instead of the estimate:

```bash
pip install tiktoken
```

## Summary comment

After the inline findings go up, the reviewer posts one top-level PR comment
with the files-reviewed count, findings grouped by severity, the estimated
cost, and the review dimensions that were enabled. The comment carries an
HTML marker (`<!-- ai-pr-reviewer-summary -->`), so re-runs find and update
that same comment in place instead of stacking duplicates.

## Config reference

Secrets and provider settings come from environment variables (no secrets in
code); review behavior can also be tuned with a `.pr-reviewer.yaml` config
file, which merges over these defaults.

| Variable | Required | Default | Description |
|---|---|---|---|
| `GITHUB_TOKEN` | yes | | Token used to fetch the diff and post the review. In Actions, use `${{ secrets.GITHUB_TOKEN }}` with `pull-requests: write` permission. |
| `LLM_API_KEY` | yes | | API key for the OpenAI-compatible LLM endpoint. |
| `LLM_BASE_URL` | no | `https://api.openai.com/v1` | Base URL of the chat completions endpoint. Point it at OpenRouter, Ollama, vLLM, etc. |
| `LLM_MODEL` | no | `gpt-4o-mini` | Model name sent to the endpoint. |
| `MAX_CHUNK_TOKENS` | no | `4000` | Max estimated tokens per diff chunk. Lower it for small-context models. |

The Action exposes the same settings as inputs: `github-token`, `llm-api-key`,
`llm-base-url`, `llm-model`, `max-chunk-tokens`, plus `dry-run`, `config-file`,
`state-path`, and `full`. A `.pr-reviewer.yaml` in your repo root is also
auto-loaded when the Action runs.

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
