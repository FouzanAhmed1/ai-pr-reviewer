"""Tests for LLM output parsing and comment formatting."""

import pytest

from reviewer.parser import ParseError, build_summary, format_comment, parse_findings

VALID_JSON = """[
  {"path": "src/app.py", "line": 12, "severity": "critical",
   "category": "security", "message": "SQL built by string concat."},
  {"path": "src/app.py", "line": 40, "severity": "nit",
   "category": "style", "message": "Rename x to something clearer."}
]"""


def test_parse_valid_findings():
    findings = parse_findings(VALID_JSON)
    assert len(findings) == 2
    assert findings[0]["severity"] == "critical"
    assert findings[0]["category"] == "security"
    assert findings[0]["line"] == 12
    assert findings[1]["path"] == "src/app.py"


def test_severity_is_case_insensitive_and_normalized():
    findings = parse_findings(
        '[{"path": "a.py", "line": 1, "severity": "MAJOR", "category": "correctness", "message": "bug"}]'
    )
    assert findings[0]["severity"] == "major"


def test_unknown_severity_drops_finding():
    findings = parse_findings(
        '[{"path": "a.py", "line": 1, "severity": "catastrophic", "category": "correctness", "message": "x"}]'
    )
    assert findings == []


def test_non_integer_line_drops_finding():
    findings = parse_findings(
        '[{"path": "a.py", "line": "12", "severity": "minor", "category": "style", "message": "x"}]'
    )
    assert findings == []


def test_tolerates_markdown_fences():
    findings = parse_findings("```json\n" + VALID_JSON + "\n```")
    assert len(findings) == 2


def test_tolerates_findings_wrapper_object():
    findings = parse_findings('{"findings": ' + VALID_JSON + "}")
    assert len(findings) == 2


def test_empty_array_parses_to_no_findings():
    assert parse_findings("[]") == []


def test_malformed_json_raises():
    with pytest.raises(ParseError):
        parse_findings("this is not json at all")


def test_non_list_json_without_findings_key_yields_none():
    assert parse_findings('{"oops": 1}') == []


def test_format_comment_includes_severity_label_and_message():
    finding = {
        "path": "src/app.py",
        "line": 12,
        "severity": "major",
        "category": "performance",
        "message": "This loop is O(n^2).",
    }
    body = format_comment(finding)
    assert "MAJOR" in body
    assert "performance" in body
    assert "O(n^2)" in body


@pytest.mark.parametrize("severity", ["critical", "major", "minor", "nit"])
def test_all_severities_render_a_label(severity):
    body = format_comment(
        {"path": "a.py", "line": 1, "severity": severity, "category": "style", "message": "m"}
    )
    assert severity in body.lower()


def test_build_summary_counts_by_severity():
    findings = [
        {"severity": "critical", "path": "a", "line": 1, "category": "security", "message": "m"},
        {"severity": "nit", "path": "a", "line": 2, "category": "style", "message": "m"},
    ]
    summary = build_summary(findings, skipped_chunks=0)
    assert "2 finding(s)" in summary
    assert "1 critical" in summary
    assert "1 nit" in summary


def test_build_summary_notes_skipped_chunks():
    summary = build_summary([], skipped_chunks=2)
    assert "2 diff chunk(s)" in summary


def test_build_summary_when_clean():
    assert "no issues found" in build_summary([], skipped_chunks=0)
