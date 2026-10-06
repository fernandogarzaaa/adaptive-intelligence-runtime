"""Generate CTC-1 task pool: Counterfactual Topology Challenge.

Design principles (per docs/PREREGISTRATION_CTC_1.md):
- AB pairs (12): byte-identical task text between A and B members.
  The ONLY difference is topology_override in the task schema.
  A: parallel_agents lacks WRITE -> infeasible.
  B: parallel_agents has WRITE-capable producer -> feasible.
- C pairs (6): same task, vary WHICH role holds WRITE.
  C_a: producer holds WRITE. C_b: synthesizer holds WRITE.
  Both feasible; tests role-name independence.
- D pairs (6): same task, add IRRELEVANT capabilities.
  D_a: no WRITE (infeasible). D_b: no WRITE + irrelevant caps.
  Both infeasible; tests invariance to irrelevant changes.

All tasks:
- Goal text contains a v1 parallel trigger ("multiple"/"several")
  so v1 -> parallel_agents (lexical control).
- Effects ["create"], 2+ fs.write ops (independent_work_unit_count > 1)
  so pv2's production rules fire.
- No fs.read ops (requested_read_effect == False).

CRITICAL: topology_override is environmental mechanics, NOT a task
feature. It is never read by extract_features (T2) and never affects
v1 scoring (T1). Both verified by structural tests.
"""

import json
import hashlib
from pathlib import Path

TASKS = []

# ---------------------------------------------------------------------------
# Topology override templates
# ---------------------------------------------------------------------------

# A member: parallel org cannot WRITE
TOPO_A_INFEASIBLE = {
    "parallel_agents": {
        "roles": ["researcher", "researcher", "synthesizer"],
        "capabilities": ["READ", "EXECUTE"],
    }
}

# B member: parallel org CAN write via producer
TOPO_B_FEASIBLE = {
    "parallel_agents": {
        "roles": ["researcher", "researcher", "producer"],
        "capabilities": ["READ", "EXECUTE", "WRITE"],
    }
}

# C_a: producer holds WRITE
TOPO_C_PRODUCER_WRITE = {
    "parallel_agents": {
        "roles": ["researcher", "researcher", "producer"],
        "capabilities": ["READ", "EXECUTE", "WRITE"],
    }
}

# C_b: synthesizer holds WRITE (different role, still feasible)
TOPO_C_SYNTH_WRITE = {
    "parallel_agents": {
        "roles": ["researcher", "synthesizer", "synthesizer"],
        "capabilities": ["READ", "EXECUTE", "WRITE"],
    }
}

# D_a: baseline infeasible (no WRITE)
TOPO_D_BASELINE = {
    "parallel_agents": {
        "roles": ["researcher", "researcher", "synthesizer"],
        "capabilities": ["READ", "EXECUTE"],
    }
}

# D_b: infeasible + IRRELEVANT capabilities (still no WRITE)
TOPO_D_IRRELEVANT = {
    "parallel_agents": {
        "roles": ["researcher", "researcher", "synthesizer"],
        "capabilities": ["READ", "EXECUTE", "DEBATE", "VERIFY", "SUMMARIZE"],
    }
}


def _claims_for(task):
    claims = []
    for i, op in enumerate(task["operations"]):
        if op["tool"] == "fs.write":
            claims.append({
                "id": f"{task['id']}-c{i}",
                "phase": op["phase"],
                "description": f"{op['args']['path']} materialized",
                "effects": ["create"],
                "artifacts": [op["args"]["path"]],
            })
    return claims


def _make_task(task_id, goal, artifacts, contents, topology_override,
               pair, pair_type):
    ops = [{"phase": "produce", "tool": "fs.write",
            "args": {"path": a, "content": c}}
           for a, c in zip(artifacts, contents)]
    task = {
        "id": task_id,
        "goal": goal,
        "artifacts": artifacts,
        "effects": ["create"],
        "operations": ops,
        "seed_files": {},
        "claims": [],
        "topology_override": topology_override,
        # CTC-1 metadata (not visible to policy; analysis only)
        "_ctc": {
            "pair": pair,
            "pair_type": pair_type,
        },
    }
    task["claims"] = _claims_for(task)
    return task


# ---------------------------------------------------------------------------
# 12 AB pairs: byte-identical text, topology is the only difference
# ---------------------------------------------------------------------------

AB_OBJECTIVES = [
    ("ctc_ab01", "Assemble the multiple release bundles for staging.",
     ["rel-a.tar", "rel-b.tar", "rel-c.tar"], ["c1", "c2", "c3"]),
    ("ctc_ab02", "Prepare the several onboarding packets for new hires.",
     ["onb-eng.md", "onb-ops.md"], ["e1", "e2"]),
    ("ctc_ab03", "Render the multiple invoice copies for accounting.",
     ["inv-101.pdf", "inv-102.pdf", "inv-103.pdf"], ["a", "b", "c"]),
    ("ctc_ab04", "Package the several migration scripts for upgrade.",
     ["mig-11.sql", "mig-12.sql"], ["m1", "m2"]),
    ("ctc_ab05", "Compile the multiple configuration sets for deploy.",
     ["cfg-a.json", "cfg-b.json", "cfg-c.json"], ["x", "y", "z"]),
    ("ctc_ab06", "Build the several test fixtures for the auth module.",
     ["fix-auth-1.json", "fix-auth-2.json"], ["p1", "p2"]),
    ("ctc_ab07", "Finalize the multiple audit entries for compliance.",
     ["aud-h1.log", "aud-h2.log", "aud-h3.log"], ["l1", "l2", "l3"]),
    ("ctc_ab08", "Emit the several status beacons for monitoring.",
     ["bcn-x.json", "bcn-y.json"], ["s1", "s2"]),
    ("ctc_ab09", "Stamp the multiple version markers on the branch.",
     ["v-3.1.tag", "v-3.2.tag", "v-3.3.tag"], ["v1", "v2", "v3"]),
    ("ctc_ab10", "Provision the several TLS certificates for edge.",
     ["edge-x.pem", "edge-y.pem"], ["t1", "t2"]),
    ("ctc_ab11", "Archive the multiple log rotations from incidents.",
     ["inc-a.log", "inc-b.log", "inc-c.log"], ["r1", "r2", "r3"]),
    ("ctc_ab12", "Mint the several API tokens for integration partners.",
     ["tok-aa.key", "tok-bb.key"], ["k1", "k2"]),
]

for pair_id, goal, artifacts, contents in AB_OBJECTIVES:
    # A member: infeasible topology
    TASKS.append(_make_task(
        f"{pair_id}a", goal, artifacts, contents,
        TOPO_A_INFEASIBLE, pair_id, "AB"))
    # B member: byte-identical text, feasible topology
    TASKS.append(_make_task(
        f"{pair_id}b", goal, artifacts, contents,
        TOPO_B_FEASIBLE, pair_id, "AB"))

# ---------------------------------------------------------------------------
# 6 C pairs: capability substitution (which role holds WRITE)
# ---------------------------------------------------------------------------

C_OBJECTIVES = [
    ("ctc_c01", "Generate the multiple summary briefs for leadership.",
     ["brf-q1.md", "brf-q2.md"], ["b1", "b2"]),
    ("ctc_c02", "Produce the several data exports for the warehouse.",
     ["exp-d1.csv", "exp-d2.csv", "exp-d3.csv"], ["d1", "d2", "d3"]),
    ("ctc_c03", "Create the multiple notification templates for alerts.",
     ["ntf-a.html", "ntf-b.html"], ["n1", "n2"]),
    ("ctc_c04", "Draft the several policy documents for legal review.",
     ["pol-01.md", "pol-02.md"], ["p1", "p2"]),
    ("ctc_c05", "Render the multiple dashboard snapshots for execs.",
     ["dash-w1.png.txt", "dash-w2.png.txt"], ["g1", "g2"]),
    ("ctc_c06", "Compile the several translation bundles for i18n.",
     ["i18n-en.json", "i18n-es.json", "i18n-fr.json"], ["i1", "i2", "i3"]),
]

for pair_id, goal, artifacts, contents in C_OBJECTIVES:
    TASKS.append(_make_task(
        f"{pair_id}a", goal, artifacts, contents,
        TOPO_C_PRODUCER_WRITE, pair_id, "C"))
    TASKS.append(_make_task(
        f"{pair_id}b", goal, artifacts, contents,
        TOPO_C_SYNTH_WRITE, pair_id, "C"))

# ---------------------------------------------------------------------------
# 6 D pairs: irrelevant capability (invariance test)
# ---------------------------------------------------------------------------

D_OBJECTIVES = [
    ("ctc_d01", "Assemble the multiple training corpora for the model.",
     ["corp-a.txt", "corp-b.txt"], ["w1", "w2"]),
    ("ctc_d02", "Prepare the several backup manifests for disaster recovery.",
     ["bkp-01.mf", "bkp-02.mf", "bkp-03.mf"], ["m1", "m2", "m3"]),
    ("ctc_d03", "Generate the multiple invoice batches for the quarter.",
     ["qb-01.pdf", "qb-02.pdf"], ["q1", "q2"]),
    ("ctc_d04", "Produce the several compliance attestations for audit.",
     ["att-a.pdf", "att-b.pdf"], ["a1", "a2"]),
    ("ctc_d05", "Create the multiple synthetic datasets for testing.",
     ["syn-1.csv", "syn-2.csv", "syn-3.csv"], ["s1", "s2", "s3"]),
    ("ctc_d06", "Draft the several incident postmortems for review.",
     ["pm-01.md", "pm-02.md"], ["z1", "z2"]),
]

for pair_id, goal, artifacts, contents in D_OBJECTIVES:
    TASKS.append(_make_task(
        f"{pair_id}a", goal, artifacts, contents,
        TOPO_D_BASELINE, pair_id, "D"))
    TASKS.append(_make_task(
        f"{pair_id}b", goal, artifacts, contents,
        TOPO_D_IRRELEVANT, pair_id, "D"))

# ---------------------------------------------------------------------------
# Hash audit fields
# ---------------------------------------------------------------------------

import sys as _sys
_sys.path.insert(0, "src")
from air.experiments.v2harness import extract_features as _ef

for t in TASKS:
    t["raw_text_hash"] = hashlib.sha256(t["goal"].encode()).hexdigest()[:16]
    _canon = json.dumps(_ef(t), sort_keys=True).encode()
    t["mechanical_features_hash"] = hashlib.sha256(_canon).hexdigest()[:16]
    _topo_canon = json.dumps(t["topology_override"], sort_keys=True).encode()
    t["topology_hash"] = hashlib.sha256(_topo_canon).hexdigest()[:16]

# Verify AB byte-identical invariant: A and B members must have
# identical goal text, artifacts, effects, operations.
for pair_id, _, _, _ in AB_OBJECTIVES:
    ta = next(t for t in TASKS if t["id"] == f"{pair_id}a")
    tb = next(t for t in TASKS if t["id"] == f"{pair_id}b")
    assert ta["goal"] == tb["goal"], f"{pair_id}: goal text differs"
    assert ta["artifacts"] == tb["artifacts"], f"{pair_id}: artifacts differ"
    assert ta["effects"] == tb["effects"], f"{pair_id}: effects differ"
    assert ta["operations"] == tb["operations"], f"{pair_id}: operations differ"
    assert ta["raw_text_hash"] == tb["raw_text_hash"]
    assert ta["mechanical_features_hash"] == tb["mechanical_features_hash"]
    assert ta["topology_hash"] != tb["topology_hash"], f"{pair_id}: topology identical"
print("AB byte-identical invariant: VERIFIED (12/12 pairs)")

# Verify C/D byte-identical text within pairs
for pair_id, _, _, _ in C_OBJECTIVES + D_OBJECTIVES:
    ta = next(t for t in TASKS if t["id"] == f"{pair_id}a")
    tb = next(t for t in TASKS if t["id"] == f"{pair_id}b")
    assert ta["goal"] == tb["goal"], f"{pair_id}: goal text differs"
    assert ta["topology_hash"] != tb["topology_hash"]
print("C/D byte-identical invariant: VERIFIED (12/12 pairs)")

# Verify v1 lexical control: all goals contain a parallel trigger
from air.allocation.allocator import _PARALLEL_WORDS
for t in TASKS:
    gl = t["goal"].lower()
    assert any(w.strip() in gl for w in _PARALLEL_WORDS), f"{t['id']}: no parallel trigger"
print("v1 lexical trigger: VERIFIED (48/48 tasks)")

# Strip internal metadata before sealing
metadata = {t["id"]: t["_ctc"] for t in TASKS}
sealed = []
for t in TASKS:
    t.pop("_ctc")
    sealed.append(t)

pool_path = Path("src/air/experiments/tasks/v2/ctc_1.json")
pool_path.write_text(json.dumps(
    {"version": "ctc-1", "tasks": sealed}, indent=1))

# Metadata stored alongside pool for analysis (NOT in task payload)
meta_path = Path("src/air/experiments/tasks/v2/ctc_1_metadata.json")
meta_path.write_text(json.dumps(metadata, indent=1))

h = hashlib.sha256(pool_path.read_bytes()).hexdigest()
print(f"Tasks: {len(sealed)}")
print(f"  AB pairs: 12 (24 tasks), C pairs: 6 (12 tasks), D pairs: 6 (12 tasks)")
print(f"SHA256: {h}")
