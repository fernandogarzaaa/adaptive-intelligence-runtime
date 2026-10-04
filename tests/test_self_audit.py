"""Tests for AIR-SELF-AUDIT v1 itself.

The critical property: the audit NEVER modifies AIR. This is enforced
structurally: every file under src/air/ is hashed before and after a
full audit run, and any byte change fails the test.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from air.audit.schema import (
    CATEGORIES,
    AuditReport,
    CategoryReport,
    Finding,
    ProbeRecord,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_AIR = REPO_ROOT / "src" / "air"


def _hash_tree() -> dict[str, str]:
    digests = {}
    for path in sorted(SRC_AIR.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digests[str(path.relative_to(REPO_ROOT))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return digests


def _finding(**over) -> Finding:
    base = dict(
        category="Security",
        finding="test finding",
        evidence=["e1"],
        confidence={"level": "high", "justification": "unit test"},
        reproduction="cmd",
        severity="low",
        recommended_change="none")
    base.update(over)
    return Finding(**base)


def test_finding_schema_exact_keys():
    f = _finding()
    assert set(f.model_dump().keys()) == {
        "category", "finding", "evidence", "confidence",
        "reproduction", "severity", "recommended_change"}


def test_finding_rejects_bad_values():
    with pytest.raises(Exception):
        _finding(severity="catastrophic")
    with pytest.raises(Exception):
        _finding(category="Astrology")
    with pytest.raises(Exception):
        _finding(confidence={"level": "vibes",
                             "justification": "trust me"})


def test_thirteen_fixed_categories():
    assert len(CATEGORIES) == 13
    assert "Reward hacking" in CATEGORIES
    assert "Prompt injection" in CATEGORIES


@pytest.fixture(scope="module")
def audit_run(tmp_path_factory):
    """Run the full audit once; snapshot src/air hashes around it."""
    out_dir = tmp_path_factory.mktemp("audit-out")
    before = _hash_tree()
    proc = subprocess.run(
        [sys.executable, "-m", "air.audit.run", "--out", str(out_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=900)
    after = _hash_tree()
    return proc, out_dir, before, after


def test_audit_completes_successfully(audit_run):
    proc, _out_dir, _before, _after = audit_run
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "complete:" in proc.stdout


def test_audit_never_modifies_air(audit_run):
    _proc, _out_dir, before, after = audit_run
    assert before.keys() == after.keys(), "audit added/removed air source files"
    changed = [k for k in before if before[k] != after[k]]
    assert not changed, f"audit modified air source files: {changed}"


def test_audit_report_structure(audit_run):
    _proc, out_dir, _before, _after = audit_run
    json_files = list(out_dir.glob("audit-*.json"))
    md_files = list(out_dir.glob("audit-*.md"))
    assert len(json_files) == 1 and len(md_files) == 1
    report = AuditReport(**json.loads(json_files[0].read_text()))
    assert len(report.categories) == 13
    for cat in report.categories:
        assert cat.name in CATEGORIES
        assert len(cat.probes) >= 1, f"{cat.name} has no probes"
        assert cat.status in ("clean", "attention")
    assert report.blind_spots.strip(), "blind spots must be documented"
    assert report.summary.strip()
    counts = report.severity_counts()
    assert sum(counts.values()) == sum(
        len(c.findings) for c in report.categories)


def test_probe_records_carry_schema(tmp_path):
    rec = ProbeRecord(name="p", category="Security", kind="live",
                      passed=True, findings=[_finding()])
    assert rec.findings[0].severity == "low"
