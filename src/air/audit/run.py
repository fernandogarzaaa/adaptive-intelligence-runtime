"""CLI entry: python -m air.audit.run --out <dir>

Runs every probe, writes audit-<id>.json and audit-<id>.md into
<dir>, prints severity counts. Exit 0 means the audit completed;
findings do not change the exit code (they are data, not errors).
A nonzero exit means the harness itself failed.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from air import __version__
from air.audit.probes import run_all
from air.audit.report import write_json, write_markdown
from air.audit.schema import AuditReport, CategoryReport
from air.events.fabric import utcnow

BLIND_SPOTS = (
    "This audit cannot see what it cannot execute. There is no model "
    "provider in this environment, so no probe exercises an "
    "instruction-following (LLM-backed) agent: prompt-injection "
    "defense is verified only at the gateway-framing layer, and "
    "reward-hacking probes use scripted behaviors, not a strategic "
    "adversary. The audit cannot observe timing side channels, "
    "operator-behavior failures, supply-chain compromise of "
    "dependencies, or multi-instance/distributed deployments (all "
    "probes run single-process against SQLite). It trusts the test "
    "suite it reuses: a test that passes vacuously (asserting "
    "nothing) would read as a clean probe. Findings are limited to "
    "the 13 fixed categories; novel failure modes outside them are "
    "out of scope by construction."
)


def build_report() -> AuditReport:
    report = AuditReport(
        audit_id=uuid.uuid4().hex[:12],
        air_version=__version__,
        started_at=utcnow(),
        blind_spots=BLIND_SPOTS)
    by_category = run_all()
    from air.audit.schema import CATEGORIES
    for name in CATEGORIES:
        probes = by_category[name]
        findings = [f for p in probes for f in p.findings]
        # A failed probe is attention-worthy even when its finding
        # was filed under another category.
        status = ("clean" if not findings
                  and all(p.passed for p in probes) else "attention")
        report.categories.append(CategoryReport(
            name=name, status=status, probes=probes, findings=findings))
    report.completed_at = utcnow()
    counts = report.severity_counts()
    total = sum(counts.values())
    report.summary = (
        f"AIR-SELF-AUDIT v1 completed at {report.completed_at}: "
        f"{total} finding(s) "
        f"({', '.join(f'{k}={v}' for k, v in counts.items())}). "
        f"{sum(1 for c in report.categories if c.status == 'clean')}/"
        f"{len(report.categories)} categories clean. "
        "Findings are reported, not applied; see blind spots for "
        "what this audit cannot see.")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AIR self-audit: findings only, never modifies AIR")
    parser.add_argument("--out", required=True,
                        help="directory for audit-<id>.json/.md")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = build_report()
    json_path = write_json(report, out_dir)
    md_path = write_markdown(report, out_dir)

    counts = report.severity_counts()
    print(f"audit {report.audit_id} complete: "
          + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
