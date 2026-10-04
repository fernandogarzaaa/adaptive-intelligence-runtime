"""Probe registry and runner for AIR-SELF-AUDIT.

Two probe kinds:
- test_file: an existing adversarial test file, executed once via
  pytest in a subprocess. Pass -> no findings (the probes run are
  recorded). Fail -> one finding per failing test file, with the
  failing test names as evidence.
- live: a function in air.audit.live run against a fresh isolated
  instance in-process.

Each unique test file executes exactly once even when it serves
several categories; every category lists the probes that ran for it.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from air.audit import live
from air.audit.schema import CATEGORIES, Finding, ProbeRecord

REPO_ROOT = Path(__file__).resolve().parents[3]

# test file -> categories it probes. A file may serve several.
TEST_FILE_PROBES: dict[str, list[str]] = {
    "tests/test_event_semantics.py": ["Architecture", "Persistence"],
    "tests/test_event_chain.py": ["Architecture", "Persistence"],
    "tests/test_causation.py": ["Architecture"],
    "tests/test_invariants.py": ["Architecture"],
    "tests/test_crash_consistency.py": ["Persistence", "Recovery"],
    "tests/test_mcp_teardown.py": ["Recovery"],
    "tests/test_security.py": ["Security", "Capability escalation"],
    "tests/test_credential_isolation.py": ["Security",
                                          "Evidence contamination"],
    "tests/test_tools_adversarial.py": ["Architecture", "Security",
                                       "Concurrency", "Prompt injection",
                                       "Capability escalation"],
    "tests/test_epistemic_separation.py": ["Epistemics", "Evaluation",
                                          "Evidence contamination"],
    "tests/test_assurance.py": ["Assurance", "Evaluation"],
    "tests/test_positive_promotion.py": ["Evaluation", "Learning"],
    "tests/test_provider_honesty.py": ["Evaluation"],
    "tests/test_policy_learning.py": ["Learning"],
    "tests/test_policy_exploit.py": ["Learning"],
    "tests/test_policy_regression.py": ["Learning"],
    "tests/test_budgets.py": ["Concurrency"],
}

# probe name -> (category, callable)
LIVE_PROBES: dict[str, tuple[str, callable]] = {
    "reward_hack_no_tool_calls":
        ("Reward hacking", live.reward_hack_no_tool_calls),
    "reward_hack_irrelevant_tool_calls":
        ("Reward hacking", live.reward_hack_irrelevant_tool_calls),
    "prompt_injection_coverage_gap":
        ("Prompt injection", live.prompt_injection_coverage_gap),
}

_PER_FILE_TIMEOUT_S = 300


@dataclass
class _FileOutcome:
    passed: bool
    detail: str
    tests_run: list[str] = field(default_factory=list)
    failed_tests: list[str] = field(default_factory=list)


def _run_test_file(relpath: str) -> _FileOutcome:
    """Execute one test file with pytest; parse pass/fail honestly."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", relpath, "-q", "--tb=no",
         "-p", "no:cacheprovider"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        timeout=_PER_FILE_TIMEOUT_S)
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    detail = " | ".join(tail) if tail else f"exit={proc.returncode}"
    if proc.returncode == 0:
        return _FileOutcome(passed=True, detail=detail,
                            tests_run=[relpath])
    failed = [ln.split("FAILED", 1)[1].strip()
              for ln in (proc.stdout or "").splitlines()
              if ln.startswith("FAILED")]
    return _FileOutcome(passed=False, detail=detail,
                        tests_run=[relpath], failed_tests=failed)


def _finding_for_failed_file(relpath: str, categories: list[str],
                             outcome: _FileOutcome) -> Finding:
    # One finding, filed under the first category; the other
    # categories reference it via their probe records.
    return Finding(
        category=categories[0],
        finding=f"adversarial test file {relpath} FAILED during the "
                f"self-audit: a boundary the suite pins is not holding",
        evidence=outcome.failed_tests or [outcome.detail],
        confidence={"level": "high",
                    "justification": "pytest exit nonzero on the "
                                     "unmodified test file"},
        reproduction=f"cd {REPO_ROOT} && "
                     f"{sys.executable} -m pytest {relpath} -q",
        severity="high",
        recommended_change="triage the failing tests, fix the boundary "
                           "in AIR (not the test), and re-run the audit")


def run_all() -> dict[str, list[ProbeRecord]]:
    """Run every probe. Returns category -> probe records."""
    assert set(CATEGORIES) == set(_all_mapped_categories()), \
        "probe registry must cover all 13 categories"

    outcomes: dict[str, _FileOutcome] = {}
    for relpath in TEST_FILE_PROBES:
        outcomes[relpath] = _run_test_file(relpath)

    by_category: dict[str, list[ProbeRecord]] = {
        c: [] for c in CATEGORIES}

    for relpath, cats in TEST_FILE_PROBES.items():
        outcome = outcomes[relpath]
        shared_finding = (None if outcome.passed
                          else _finding_for_failed_file(
                              relpath, cats, outcome))
        for cat in cats:
            findings = ([shared_finding] if shared_finding
                        and shared_finding.category == cat else [])
            # Categories after the first still see the failure in
            # their probe detail line.
            detail = outcome.detail
            if shared_finding and shared_finding.category != cat:
                detail += (f" [FAILED; finding filed under "
                           f"{shared_finding.category}]")
            by_category[cat].append(ProbeRecord(
                name=relpath, category=cat, kind="test_file",
                passed=outcome.passed, detail=detail,
                tests_run=outcome.tests_run, findings=findings))

    for name, (cat, fn) in LIVE_PROBES.items():
        result = fn()
        by_category[cat].append(ProbeRecord(
            name=name, category=cat, kind="live",
            passed=result.passed, detail=result.detail,
            tests_run=[name], findings=result.findings))

    return by_category


def _all_mapped_categories() -> set[str]:
    cats = set()
    for v in TEST_FILE_PROBES.values():
        cats.update(v)
    for cat, _ in LIVE_PROBES.values():
        cats.add(cat)
    return cats
