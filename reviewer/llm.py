"""Talk to any OpenAI-compatible chat-completions endpoint with plain HTTP.

No provider SDKs. The system prompt asks for strict JSON so findings parse
reliably; malformed output is retried once by the caller before the chunk is
skipped with a warning.
"""

import requests

REQUEST_TIMEOUT = 120

SYSTEM_PROMPT = """\
You are a senior software engineer reviewing a pull request diff.
Review the diff chunks you receive and find issues worth flagging.

Cover these dimensions:
- Correctness / bugs: wrong logic, off-by-one errors, broken edge cases, bad error handling
- Security: injection, auth/authz flaws, secrets in code, unsafe deserialization, SSRF, XSS, command injection
- Performance: obvious complexity blowups, N+1 patterns, unbounded loops, missing pagination, hot-path allocations
- Code style: naming, dead code, duplication, readability problems worth fixing

Rules:
- Only flag issues you are reasonably confident about. No speculation, no "consider" filler.
- Respond with ONLY a JSON array. No markdown fences, no prose.
- Each finding is an object with exactly these keys:
  - "path": the file path from the diff header (e.g. "src/app.py")
  - "line": the NEW-file line number of the offending line (integer). Only reference added ("+") lines from the diff. Omit the object if you cannot pin a line.
  - "severity": one of "critical", "major", "minor", "nit"
  - "category": one of "correctness", "security", "performance", "style"
  - "message": one or two sentences describing the issue and, when obvious, how to fix it.
- If there is nothing worth flagging, return an empty array [].
- Severity guide: critical = exploitable or data loss or definitely broken; major = likely bug or real security/perf concern; minor = probable issue or meaningful readability win; nit = tiny style preference.
"""


class LLMError(Exception):
    """Raised when the LLM endpoint call fails."""


def build_messages(pr_context, chunk_text):
    """Build the chat messages for one diff chunk."""
    user_content = (
        f"Pull request: {pr_context.get('title', '')}\n"
        f"Description: {(pr_context.get('body', '') or '')[:2000]}\n\n"
        f"Diff chunk:\n```diff\n{chunk_text}\n```"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def review_chunk(base_url, api_key, model, pr_context, chunk_text):
    """Send one diff chunk to the LLM and return the raw response text."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": build_messages(pr_context, chunk_text),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc
    if resp.status_code != 200:
        raise LLMError(f"LLM API returned {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"LLM response was not a valid chat-completions payload: {exc}") from exc
