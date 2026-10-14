"""Parse structured LLM review output and format GitHub review comments."""

import json
import logging

from .config import SEVERITIES

log = logging.getLogger(__name__)

CATEGORIES = ("correctness", "security", "performance", "style")

SEVERITY_EMOJI = {
    "critical": "CRITICAL",
    "major": "MAJOR",
    "minor": "minor",
    "nit": "nit",
}


class ParseError(Exception):
    """Raised when LLM output cannot be parsed into findings."""


def _normalize_severity(raw):
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return value if value in SEVERITIES else None


def _normalize_category(raw):
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    return value if value in CATEGORIES else None


def parse_findings(raw_text):
    """Parse the LLM's JSON response into a list of validated finding dicts.

    Tolerates markdown fences and a top-level {"findings": [...]} wrapper.
    Invalid entries are dropped, never fatal.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        # Strip fenced code blocks the model sometimes adds anyway.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"LLM output is not valid JSON: {exc}") from exc

    if isinstance(data, dict):
        data = data.get("findings", data.get("results", []))
    if not isinstance(data, list):
        raise ParseError("LLM output JSON must be an array of findings.")

    findings = []
    for item in data:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        line = item.get("line")
        severity = _normalize_severity(item.get("severity"))
        category = _normalize_category(item.get("category"))
        message = item.get("message")
        if not path or not isinstance(line, int) or not severity or not category or not message:
            log.warning("Dropping malformed finding: %r", item)
            continue
        findings.append(
            {
                "path": str(path),
                "line": line,
                "severity": severity,
                "category": category,
                "message": str(message).strip(),
            }
        )
    return findings


def format_comment(finding):
    """Render one finding as a GitHub review comment body.

    The severity label is always included so it is visible at a glance.
    """
    label = SEVERITY_EMOJI[finding["severity"]]
    return f"**[{label}]** ({finding['category']})\n\n{finding['message']}"


def build_summary(findings, skipped_chunks):
    """Render the review-level summary body posted above the inline comments."""
    if not findings and not skipped_chunks:
        return "AI review complete: no issues found."
    counts = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    parts = [f"{counts.get(s, 0)} {s}" for s in SEVERITIES if counts.get(s)]
    lines = [f"AI review complete: {len(findings)} finding(s) ({', '.join(parts)})."]
    if skipped_chunks:
        lines.append(
            f"Note: {skipped_chunks} diff chunk(s) could not be parsed and were skipped."
        )
    return "\n".join(lines)
