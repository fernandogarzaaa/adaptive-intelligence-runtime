"""Report writers for AIR-SELF-AUDIT. Output is confined to --out."""

from __future__ import annotations

import json
from pathlib import Path

from air.audit.schema import AuditReport


def _confine(out_dir: Path, name: str) -> Path:
    out_dir = out_dir.resolve()
    target = (out_dir / name).resolve()
    if target.parent != out_dir:
        raise ValueError(f"refusing to write outside --out: {name!r}")
    return target


def write_json(report: AuditReport, out_dir: Path) -> Path:
    target = _confine(out_dir, f"audit-{report.audit_id}.json")
    target.write_text(json.dumps(report.model_dump(), indent=2) + "\n",
                      encoding="utf-8")
    return target


def write_markdown(report: AuditReport, out_dir: Path) -> Path:
    target = _confine(out_dir, f"audit-{report.audit_id}.md")
    lines = [
        f"# AIR self-audit {report.audit_id}",
        "",
        f"AIR version: {report.air_version}",
        f"Started: {report.started_at}",
        f"Completed: {report.completed_at}",
        "",
        "This audit reports findings only. It does not modify AIR; "
        "`recommended_change` fields are text, never applied.",
        "",
        "## Severity counts",
        "",
    ]
    for sev, count in report.severity_counts().items():
        lines.append(f"- {sev}: {count}")
    lines.append("")
    for cat in report.categories:
        lines.append(f"## {cat.name} [{cat.status}]")
        lines.append("")
        for probe in cat.probes:
            mark = "PASS" if probe.passed else "FAIL"
            lines.append(f"### probe `{probe.name}` ({probe.kind}) [{mark}]")
            if probe.detail:
                lines.append(f"> {probe.detail}")
            lines.append("")
        if not cat.findings:
            lines.append("No findings.")
            lines.append("")
        for f in cat.findings:
            lines += [
                f"### finding [{f.severity}]",
                "",
                f"{f.finding}",
                "",
                f"confidence: {f.confidence.level} "
                f"({f.confidence.justification})",
                "",
                "**evidence**",
                "",
            ]
            lines += [f"- {e}" for e in f.evidence] or ["- (none)"]
            lines += [
                "",
                "**reproduction**",
                "",
                f"`{f.reproduction}`",
                "",
                "**recommended change** (not applied)",
                "",
                f.recommended_change,
                "",
            ]
    lines += ["## Blind spots", "", report.blind_spots, "",
              "## Summary", "", report.summary, ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
