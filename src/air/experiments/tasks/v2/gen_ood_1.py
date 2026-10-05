"""Generate OOD-1 task pool: adversarial lexical shift with mechanical preservation.

Design principles:
- Production tasks: no read effect, write/execute required, multiple artifacts.
  Mechanically identical to D-series production, but goal text avoids ALL
  v1 lexical triggers (_PARALLEL_WORDS, _RESEARCH_WORDS, etc.).
- Research tasks: read-heavy, observe effects. Avoid research trigger words.
- Minimal pairs:
  - Mechanical-contrast: same surface template, different effects.
  - Lexical-contrast: different surface, same effects.

Banned in goal text (v1 _PARALLEL_WORDS and related):
  each, multiple, several, independently, three, 3, list of,
  create, write, generate, produce, file, files,
  read, inspect, check, review, examine
"""

import json
import hashlib
from pathlib import Path

TASKS = []


def add(task_id, goal, artifacts, effects, operations, seed_files=None,
        pair=None, pair_type=None, expected_org=None):
    TASKS.append({
        "id": task_id,
        "goal": goal,
        "artifacts": artifacts,
        "effects": effects,
        "operations": operations,
        "seed_files": seed_files or {},
        "claims": [],  # filled below
        # OOD-1 metadata (not visible to policy)
        "_ood": {
            "pair": pair,
            "pair_type": pair_type,
            "expected_organization": expected_org,
        },
    })


def _claims_for(task):
    """Build claims from operations (same convention as D-series)."""
    claims = []
    for i, op in enumerate(task["operations"]):
        phase = op["phase"]
        tool = op["tool"]
        if tool == "fs.write":
            claims.append({
                "id": f"{task['id']}-c{i}",
                "phase": phase,
                "description": f"{op['args']['path']} materialized",
                "effects": ["create"],
                "artifacts": [op["args"]["path"]],
            })
        elif tool == "fs.read":
            claims.append({
                "id": f"{task['id']}-c{i}",
                "phase": phase,
                "description": "inputs observed",
                "effects": ["observe"],
                "artifacts": [],
            })
        elif tool == "shell.exec":
            claims.append({
                "id": f"{task['id']}-c{i}",
                "phase": phase,
                "description": "command executed",
                "effects": ["execute"],
                "artifacts": [],
            })
    return claims


# ---------------------------------------------------------------------------
# Production-OOD (16): write/execute, no read, multiple artifacts.
# Surface forms avoid: three/two/multiple/create/write/file/generate/produce.
# ---------------------------------------------------------------------------

PROD_OOD = [
    ("ood_p01", "Assemble the deployment bundle for staging.",
     ["bundle-a.tar", "bundle-b.tar", "bundle-c.tar"],
     ["c1", "c2", "c3"]),
    ("ood_p02", "Prepare release artifacts for the QA environment.",
     ["qa-build.zip", "qa-notes.md"],
     ["q1", "q2"]),
    ("ood_p03", "Render invoice copies for the accounting period.",
     ["inv-001.pdf", "inv-002.pdf", "inv-003.pdf", "inv-004.pdf"],
     ["a", "b", "c", "d"]),
    ("ood_p04", "Package migration scripts for the database upgrade.",
     ["mig-01.sql", "mig-02.sql"],
     ["m1", "m2"]),
    ("ood_p05", "Compile the configuration set for production.",
     ["prod.cfg", "prod.secrets", "prod.routes"],
     ["x", "y", "z"]),
    ("ood_p06", "Draft onboarding packets for new hires.",
     ["onboard-eng.md", "onboard-ops.md"],
     ["e1", "e2"]),
    ("ood_p07", "Build test fixtures for the payment module.",
     ["fix-pay-1.json", "fix-pay-2.json", "fix-pay-3.json"],
     ["p1", "p2", "p3"]),
    ("ood_p08", "Finalize audit trail entries for compliance.",
     ["audit-q3.log", "audit-q4.log"],
     ["l1", "l2"]),
    ("ood_p09", "Emit status beacons for the monitoring dashboard.",
     ["beacon-a.json", "beacon-b.json", "beacon-c.json"],
     ["s1", "s2", "s3"]),
    ("ood_p10", "Stamp version markers across the release branch.",
     ["ver-2.1.tag", "ver-2.2.tag"],
     ["v1", "v2"]),
    ("ood_p11", "Provision TLS certificates for the edge nodes.",
     ["edge-1.pem", "edge-2.pem", "edge-3.pem"],
     ["t1", "t2", "t3"]),
    ("ood_p12", "Seed the demo tenant with sample records.",
     ["demo-users.csv", "demo-orders.csv"],
     ["d1", "d2"]),
    ("ood_p13", "Archive log rotations for the incident window.",
     ["inc-2026-10-01.log", "inc-2026-10-02.log", "inc-2026-10-03.log"],
     ["r1", "r2", "r3"]),
    ("ood_p14", "Publish the changelog digest for stakeholders.",
     ["digest-oct.md", "digest-nov.md"],
     ["g1", "g2"]),
    ("ood_p15", "Mint API tokens for the integration partners.",
     ["tok-acme.key", "tok-globex.key", "tok-initech.key"],
     ["k1", "k2", "k3"]),
    ("ood_p16", "Snapshot the feature flags for rollback safety.",
     ["flags-before.json", "flags-after.json"],
     ["f1", "f2"]),
]

for tid, goal, artifacts, contents in PROD_OOD:
    ops = [{"phase": "produce", "tool": "fs.write",
            "args": {"path": a, "content": c}}
           for a, c in zip(artifacts, contents)]
    add(tid, goal, artifacts, ["create"], ops,
        expected_org="single_agent")

# ---------------------------------------------------------------------------
# Research-OOD (16): read-heavy, observe effects only.
# Avoid: read/inspect/check/review/examine in goal text.
# ---------------------------------------------------------------------------

RES_OOD = [
    ("ood_r01", "Survey the dependency declarations for outdated pins.",
     ["dep-a.txt", "dep-b.txt"], ["d1 d2", "d3 d4"]),
    ("ood_r02", "Peruse the access logs for anomalous patterns.",
     ["acc-01.log", "acc-02.log", "acc-03.log"], ["l1", "l2", "l3"]),
    ("ood_r03", "Study the schema definitions for migration planning.",
     ["sch-v1.json", "sch-v2.json"], ["s1", "s2"]),
    ("ood_r04", "Scan the incident timeline for root-cause clues.",
     ["tl-01.md", "tl-02.md"], ["t1", "t2"]),
    ("ood_r05", "Browse the vendor catalog for pricing updates.",
     ["cat-q3.csv", "cat-q4.csv", "cat-q1.csv"], ["c1", "c2", "c3"]),
    ("ood_r06", "Look over the test coverage summaries.",
     ["cov-api.txt", "cov-ui.txt"], ["v1", "v2"]),
    ("ood_r07", "Go through the onboarding feedback forms.",
     ["fb-01.txt", "fb-02.txt", "fb-03.txt"], ["f1", "f2", "f3"]),
    ("ood_r08", "Leaf through the architecture decision records.",
     ["adr-001.md", "adr-002.md"], ["a1", "a2"]),
    ("ood_r09", "Parse the deployment manifests for drift.",
     ["dep-staging.yaml", "dep-prod.yaml"], ["y1", "y2"]),
    ("ood_r10", "Skim the customer transcripts for feature requests.",
     ["tr-101.txt", "tr-102.txt", "tr-103.txt"], ["x1", "x2", "x3"]),
    ("ood_r11", "Audit the permission grants for overprivileged roles.",
     ["perm-a.json", "perm-b.json"], ["p1", "p2"]),
    ("ood_r12", "Canvass the status pages for outage notices.",
     ["st-oct.html", "st-nov.html"], ["h1", "h2"]),
    ("ood_r13", "Sift the error budgets for burn-rate anomalies.",
     ["eb-01.csv", "eb-02.csv", "eb-03.csv"], ["e1", "e2", "e3"]),
    ("ood_r14", "Delve into the runbook procedures for failover steps.",
     ["rb-01.md", "rb-02.md"], ["r1", "r2"]),
    ("ood_r15", "Glance at the sprint retrospectives for action items.",
     ["retro-12.md", "retro-13.md", "retro-14.md"], ["m1", "m2", "m3"]),
    ("ood_r16", "Pore over the contract amendments for renewal terms.",
     ["ctr-a.pdf.txt", "ctr-b.pdf.txt"], ["z1", "z2"]),
]

for tid, goal, artifacts, contents in RES_OOD:
    ops = [{"phase": "prepare", "tool": "fs.read",
            "args": {"path": a}}
           for a in artifacts]
    seeds = {a: c for a, c in zip(artifacts, contents)}
    add(tid, goal, [], ["observe"], ops, seed_files=seeds,
        expected_org="parallel_agents")

# ---------------------------------------------------------------------------
# Minimal pairs (8).
#
# Mechanical-contrast (4): same surface template, different effects.
#   If pv2 distinguishes, it uses mechanical features.
# Lexical-contrast (4): different surface, same effects.
#   If pv2 generalizes, the mechanical rules are lexically robust.
# ---------------------------------------------------------------------------

# Pair M1: same template "Handle the ___ batch", production vs research
add("ood_m1a", "Handle the multiple outbound batches for delivery.",
    ["out-1.pkg", "out-2.pkg"], ["create"],
    [{"phase": "produce", "tool": "fs.write",
      "args": {"path": "out-1.pkg", "content": "p1"}},
     {"phase": "produce", "tool": "fs.write",
      "args": {"path": "out-2.pkg", "content": "p2"}}],
    pair="M1", pair_type="mechanical-contrast",
    expected_org="single_agent")
add("ood_m1b", "Handle the multiple inbound batches for triage.",
    [], ["observe"],
    [{"phase": "prepare", "tool": "fs.read",
      "args": {"path": "in-1.pkg"}},
     {"phase": "prepare", "tool": "fs.read",
      "args": {"path": "in-2.pkg"}}],
    seed_files={"in-1.pkg": "i1", "in-2.pkg": "i2"},
    pair="M1", pair_type="mechanical-contrast",
    expected_org="parallel_agents")

# Pair M2: same template "Process the quarterly ___", production vs research
add("ood_m2a", "Process the several quarterly statements for distribution.",
    ["q3-stmt.pdf", "q4-stmt.pdf"], ["create"],
    [{"phase": "produce", "tool": "fs.write",
      "args": {"path": "q3-stmt.pdf", "content": "s3"}},
     {"phase": "produce", "tool": "fs.write",
      "args": {"path": "q4-stmt.pdf", "content": "s4"}}],
    pair="M2", pair_type="mechanical-contrast",
    expected_org="single_agent")
add("ood_m2b", "Process the several quarterly surveys for analysis.",
    [], ["observe"],
    [{"phase": "prepare", "tool": "fs.read",
      "args": {"path": "q3-surv.csv"}},
     {"phase": "prepare", "tool": "fs.read",
      "args": {"path": "q4-surv.csv"}}],
    seed_files={"q3-surv.csv": "v3", "q4-surv.csv": "v4"},
    pair="M2", pair_type="mechanical-contrast",
    expected_org="parallel_agents")

# Pair L1: lexical-contrast, both production (no read effect)
#   Surface A uses D-series-like phrasing; surface B is OOD-shifted.
add("ood_l1a", "Turn out the monthly summaries for the leadership sync.",
    ["sum-oct.md", "sum-nov.md"], ["create"],
    [{"phase": "produce", "tool": "fs.write",
      "args": {"path": "sum-oct.md", "content": "o"}},
     {"phase": "produce", "tool": "fs.write",
      "args": {"path": "sum-nov.md", "content": "n"}}],
    pair="L1", pair_type="lexical-contrast",
    expected_org="single_agent")
add("ood_l1b", "Crank through the per-diem expense bundles ahead of audit.",
    ["exp-w42.zip", "exp-w43.zip"], ["create"],
    [{"phase": "produce", "tool": "fs.write",
      "args": {"path": "exp-w42.zip", "content": "e1"}},
     {"phase": "produce", "tool": "fs.write",
      "args": {"path": "exp-w43.zip", "content": "e2"}}],
    pair="L1", pair_type="lexical-contrast",
    expected_org="single_agent")

# Pair L2: lexical-contrast, both research (read-heavy)
add("ood_l2a", "Reconstruct the timeline from the fragmented event logs.",
    [], ["observe"],
    [{"phase": "prepare", "tool": "fs.read",
      "args": {"path": "ev-01.log"}},
     {"phase": "prepare", "tool": "fs.read",
      "args": {"path": "ev-02.log"}}],
    seed_files={"ev-01.log": "a", "ev-02.log": "b"},
    pair="L2", pair_type="lexical-contrast",
    expected_org="parallel_agents")
add("ood_l2b", "Piece together what happened from the scattered debug traces.",
    [], ["observe"],
    [{"phase": "prepare", "tool": "fs.read",
      "args": {"path": "dbg-01.txt"}},
     {"phase": "prepare", "tool": "fs.read",
      "args": {"path": "dbg-02.txt"}}],
    seed_files={"dbg-01.txt": "x", "dbg-02.txt": "y"},
    pair="L2", pair_type="lexical-contrast",
    expected_org="parallel_agents")

# Fill claims for all tasks
for t in TASKS:
    t["claims"] = _claims_for(t)

# Attach hash audit fields (raw text vs mechanical features, separately)
import hashlib as _hl
import sys as _sys
_sys.path.insert(0, "src")
from air.experiments.v2harness import extract_features as _ef
for t in TASKS:
    t["raw_text_hash"] = _hl.sha256(t["goal"].encode()).hexdigest()[:16]
    _canon = json.dumps(_ef(t), sort_keys=True).encode()
    t["mechanical_features_hash"] = _hl.sha256(_canon).hexdigest()[:16]

# Strip internal metadata before sealing (keep expected_org for analysis,
# but NOT visible to the policy at runtime)
metadata = {t["id"]: t["_ood"] for t in TASKS}
sealed = []
for t in TASKS:
    t.pop("_ood")
    sealed.append(t)

out = {"version": "ood-1", "tasks": sealed,
       "_analysis_metadata": metadata}

# Write sealed pool (without metadata) and metadata separately
pool_path = Path("src/air/experiments/tasks/v2/ood_1.json")
pool_path.write_text(json.dumps(
    {"version": "ood-1", "tasks": sealed}, indent=1))

meta_path = Path("/tmp/ood_1_metadata.json")
meta_path.write_text(json.dumps(metadata, indent=1))

h = hashlib.sha256(pool_path.read_bytes()).hexdigest()
print(f"Tasks: {len(sealed)}")
print(f"  production-OOD: 16, research-OOD: 16, minimal pairs: 8")
print(f"SHA256: {h}")
print(f"Metadata (analysis only): {meta_path}")

# Lexical audit: confirm banned words absent from goal text
BANNED = {"three", "two", "four", "five", "multiple", "several",
          "create", "write", "generate", "produce",
          "file", "files", "document", "report", "manifest",
          "read", "inspect", "check", "review", "examine",
          "each", "independently", "list of"}
import re
violations = []
for t in sealed:
    words = set(re.findall(r"[a-z]+", t["goal"].lower()))
    hit = words & BANNED
    # "list of" is a phrase; check separately
    if "list of" in t["goal"].lower():
        hit = hit | {"list of"}
    if hit:
        violations.append((t["id"], hit))
if violations:
    print("LEXICAL VIOLATIONS:")
    for tid, hit in violations:
        print(f"  {tid}: {hit}")
else:
    print("Lexical audit: CLEAN (no banned words in goal text)")
