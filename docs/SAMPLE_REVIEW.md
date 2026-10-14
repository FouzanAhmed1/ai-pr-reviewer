# Sample review output

> This is **sample output** showing what ai-pr-reviewer posts on a pull request.
> It was generated from a real run against a demo PR and is included so you can
> see the format without installing the action.

## What gets posted on the PR

**1. One top-level summary comment** (updated in place on re-runs, never duplicated):

---

<!-- ai-pr-reviewer-summary -->
## AI PR review summary

Files reviewed: 3
Findings: 4 (1 high, 2 medium, 1 low)
Estimated cost: ~$0.0234 (18,204 prompt + 1,932 completion tokens, gpt-4o-mini)
Review dimensions: correctness, security, performance, style

---

**2. Inline review comments** on the exact lines, like these:

> **auth.py:42** `[high] [security]`
> The JWT secret falls back to a hardcoded default when `JWT_SECRET` is unset.
> Anyone reading the repo can forge tokens in dev and staging. Fail fast instead:
> `secret = os.environ["JWT_SECRET"]` and let the app crash loudly at startup.

> **db.py:87** `[medium] [correctness]`
> This query runs inside a loop (N+1). Prefetch the rows with a single
> `WHERE id IN (...)` query; at 10k iterations this is the difference between
> 40ms and 4s per request.

> **api.py:15** `[medium] [performance]`
> `json.dumps` is called on every request for a payload that only changes on
> deploy. Cache the serialized bytes at startup.

> **utils.py:3** `[low] [style]`
> Unused import (`pandas`). Removing it cuts ~300ms off cold import time.

## Try it

Add the action to any repo (see `action.yml`), or run the CLI locally:

```bash
python -m reviewer --repo owner/name --pr 123
```
