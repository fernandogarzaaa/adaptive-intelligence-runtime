"""Benchmark report builder (docs/RESEARCH_PROTOCOL.md section 5).

Reads the registry, refuses to build unless post-run protocol
verification passed, aggregates per-condition metrics from the
immutable run records, and writes ``report.md`` + ``report.json``
into the experiment directory. Appends a ``report`` record listing
every covered run_id (the no-filtering check compares this against
the registry).

The report states the research question up front, reports the
outcome whichever way it falls (A>B>C, A~B~C, and C<A are all
legitimate scientific results), and carries a full failure ledger:
failures are first-class results.

Usage:
    python -m air.experiments.report --exp-dir <dir>
"""

from __future__ import annotations

import json
import os
import sys

from air.experiments import metrics, prereg, registry
from air.experiments.conditions import CONDITIONS


def _require_post_verification(exp_dir: str) -> None:
    recs = [r for r in registry.load(exp_dir, "protocol_verification")
            if r["payload"].get("phase") == "post"]
    if not recs or not recs[-1]["payload"].get("passed"):
        raise RuntimeError(
            "no passing post-run protocol_verification record; run "
            "python -m air.experiments.verify_protocol --phase post first")


def _f(x, nd=3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def build_report(exp_dir: str) -> dict:
    _require_post_verification(exp_dir)
    run_recs = registry.load(exp_dir, "run")
    prereg_recs = registry.load(exp_dir, "pre_registration")
    pre = prereg_recs[0]["payload"] if prereg_recs else {}

    by_cond: dict[str, list[dict]] = {}
    for r in run_recs:
        p = r["payload"]
        by_cond.setdefault(p["condition"], []).append(p["metrics"])

    aggs = {c: metrics.aggregate(by_cond.get(c, []))
            for c in sorted(by_cond)}
    # Pairwise significance on verified successes (Fisher's exact).
    sig = {}
    for x, y in (("C", "A"), ("C", "B"), ("B", "A")):
        if x in aggs and y in aggs:
            ax, ay = aggs[x], aggs[y]
            sig[f"{x}_vs_{y}"] = {
                "p_value": metrics.fisher_exact(
                    ax["verified_successes"],
                    ax["n_runs"] - ax["verified_successes"],
                    ay["verified_successes"],
                    ay["n_runs"] - ay["verified_successes"]),
                "note": "Fisher's exact, two-sided, on verified "
                        "success/failure counts",
            }

    # Failure ledger: every run that is not a verified success.
    failures = []
    for r in run_recs:
        p = r["payload"]
        m = p["metrics"]
        if not m["verified_success"]:
            failures.append({
                "run_id": p["run_id"],
                "condition": p["condition"],
                "task_id": p["task_id"],
                "repetition": p["repetition"],
                "verdict": m["verdict"],
                "assurance_system_verdict": m["assurance_system_verdict"],
                "run_status": m["run_status"],
                "wall_exceeded": p["wall_exceeded"],
                "n_agents": m["n_agents"],
                "n_tool_completed": m["n_tool_completed"],
                "ledger": p["ledger"],
            })

    report = {
        "exp_id": pre.get("exp_id", "?"),
        "research_question": pre.get("research_question",
                                    prereg.RESEARCH_QUESTION),
        "task_set": pre.get("task_set", {}),
        "held_constant": pre.get("held_constant", {}),
        "conditions": pre.get("conditions", {}),
        "metrics_declared": pre.get("metrics", []),
        "per_condition": aggs,
        "significance": sig,
        "failure_ledger": failures,
        "n_failures": len(failures),
        "n_runs": len(run_recs),
        "policy_versions": sorted(
            {r["payload"]["policy_version"] for r in run_recs}),
        "not_measured": [
            "Model-backed competence: no model provider exists in this "
            "environment; all agents ran scripted behaviors with identical "
            "competence by construction. The experiment varies organization "
            "only, not agent capability.",
            "Mid-run spawning/adaptation: scripted behaviors never call "
            "spawn_agent; condition C's dynamism is the allocator's "
            "per-task strategy choice. The D1->experience/learning->D2->D3 "
            "sequence is the follow-up that exercises adaptation.",
            "Content-level truth of artifacts: the evidence model is "
            "structural (validity + relevance); a well-formed file with "
            "wrong contents still grounds. By design; documented in "
            "src/air/evidence/relevance.py.",
            "Unnecessary spawning by ablation: no mid-run spawns occurred, "
            "so the ablation measure is vacuous here (reported as 0 by "
            "construction).",
            "Capability transfer across domains and policy regressions: "
            "learning is off; no capabilities promoted.",
            "Cost in USD/tokens: moot with scripted behaviors (recorded "
            "as 0); latency is harness-measured wall time.",
        ],
        "ledger_pointers": sorted(
            {r["payload"]["ledger"] for r in run_recs}),
    }
    return report


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def render_markdown(report: dict) -> str:
    L: list[str] = []
    L.append(f"# Baseline experiment report: {report['exp_id']}")
    L.append("")
    L.append(f"**Research question.** {report['research_question']}")
    L.append("")
    ts = report["task_set"]
    L.append(f"Task set **{ts.get('version')}**: {ts.get('n_tasks')} tasks, "
             f"sha256 `{ts.get('sha256')}`")
    L.append("")
    L.append("## Per-condition results")
    L.append("")
    headers = ["metric", "A (single)", "B (static)", "C (dynamic)"]
    rows = []
    aggs = report["per_condition"]
    def col(c, key, nd=3):
        return _f(aggs[c][key], nd) if c in aggs else "n/a"
    metric_rows = [
        ("runs", "n_runs", 0),
        ("verified successes", "verified_successes", 0),
        ("verified success rate", "verified_success_rate", 4),
        ("invalid/inconclusive rate", "invalid_inconclusive_rate", 4),
        ("verified+sound", "verified_and_sound", 0),
        ("cost / verified success (USD)", "cost_per_verified_success", 4),
        ("latency / verified success (s)", "latency_per_verified_success", 2),
        ("mean latency / run (s)", "mean_latency_s", 2),
        ("total tokens", "total_tokens", 0),
        ("mean agents / run", "mean_agents", 2),
        ("total agents", "total_agents", 0),
        ("spawn efficiency (verified/agent)", "spawn_efficiency", 4),
        ("spawn requested", "spawn_requested", 0),
        ("spawn approved", "spawn_approved", 0),
        ("spawn denied", "spawn_denied", 0),
        ("unnecessary spawning", "unnecessary_spawning", 0),
        ("verification failures", "verification_failures", 0),
        ("evaluator failures", "evaluator_failures", 0),
        ("unexpected failures", "unexpected_failures", 0),
        ("tool denied", "tool_denied", 0),
        ("policy blocked", "policy_blocked", 0),
        ("rollbacks", "rollbacks", 0),
        ("policy changes", "policy_changes", 0),
    ]
    for label, key, nd in metric_rows:
        rows.append([label, col("A", key, nd), col("B", key, nd),
                     col("C", key, nd)])
    L.append(_md_table(headers, rows))
    L.append("")
    L.append("### Verdict buckets")
    L.append("")
    all_verdicts = sorted({v for a in aggs.values()
                           for v in a["verdict_buckets"]})
    rows = [[v] + [str(aggs[c]["verdict_buckets"].get(v, 0))
                   if c in aggs else "n/a" for c in ("A", "B", "C")]
            for v in all_verdicts]
    L.append(_md_table(["verdict", "A", "B", "C"], rows))
    L.append("")
    L.append("### Significance (verified success counts)")
    L.append("")
    for pair, s in report["significance"].items():
        L.append(f"- {pair}: Fisher's exact two-sided p = {_f(s['p_value'], 4)}")
    L.append("")
    L.append("## Failure ledger (first-class results)")
    L.append("")
    L.append(f"{report['n_failures']} of {report['n_runs']} runs were not "
             f"verified successes.")
    L.append("")
    if report["failure_ledger"]:
        rows = [[f["run_id"][:16] + "...", f["condition"], f["task_id"],
                 str(f["repetition"]), f["verdict"],
                 str(f["assurance_system_verdict"]), f["run_status"]]
                for f in report["failure_ledger"]]
        L.append(_md_table(
            ["run_id", "cond", "task", "rep", "verdict", "assurance",
             "run status"], rows))
    L.append("")
    L.append("## Pins and provenance")
    L.append("")
    L.append(f"- Policy versions observed: {report['policy_versions']}")
    L.append(f"- Ledgers retained per run under: "
             f"{', '.join(report['ledger_pointers'][:3])} ...")
    L.append(f"- Capability reuse: "
             f"{aggs.get('A', {}).get('capability_reuse', 'n/a')}")
    L.append("")
    L.append("## What was not measured (and why)")
    L.append("")
    for item in report["not_measured"]:
        L.append(f"- {item}")
    L.append("")
    L.append("## Interpretation")
    L.append("")
    L.append("Report the outcome whichever way it falls: A>B>C, A~B~C, "
             "and C<A are all legitimate scientific results. A C<A result "
             "would indicate a measurable coordination tax on the dynamic "
             "allocator under these conditions. See report.json for the "
             "full numbers.")
    L.append("")
    return "\n".join(L)


def write_report(exp_dir: str) -> dict:
    """Build the report; append the report record; return paths."""
    report = build_report(exp_dir)
    md = render_markdown(report)
    md_path = os.path.join(exp_dir, "report.md")
    json_path = os.path.join(exp_dir, "report.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(report, indent=2, sort_keys=True))
    run_ids = [r["payload"]["run_id"]
               for r in registry.load(exp_dir, "run")]
    rec = registry.append(exp_dir, "report", {
        "report_md": "report.md",
        "report_json": "report.json",
        "n_runs": len(run_ids),
        "n_failures": report["n_failures"],
        "run_ids": sorted(run_ids),
    })
    return {"report_md": md_path, "report_json": json_path,
            "record_seq": rec["seq"]}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Build the benchmark report")
    ap.add_argument("--exp-dir", required=True)
    args = ap.parse_args()
    out = write_report(args.exp_dir)
    print(f"report written: {out['report_md']}")
    print(f"record seq: {out['record_seq']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
