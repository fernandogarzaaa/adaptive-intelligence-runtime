"""Generate the preregistered v2 task sets (D1/S1/D2/S2/D3).

Deterministic: every task is explicit. No `kind` field anywhere: the
v2 learner's feature extractor derives class-A features mechanically
from operations/effects/artifacts only. Task IDs are unique across
all five sets; sets are disjoint by construction.

Run: python3 gen_v2_tasks.py  (writes tasks/*.json + SHA256SUM)
"""

import hashlib
import json
from pathlib import Path

OUT = Path(__file__).parent


def write_task(tid, goal, artifacts, effects, operations,
               claims, seed_files=None):
    return {
        "id": tid,
        "goal": goal,
        "artifacts": artifacts,
        "effects": effects,
        "operations": operations,
        "claims": claims,
        "seed_files": seed_files or {},
    }


def wop(path, content):
    return {"phase": "produce", "tool": "fs.write",
            "args": {"path": path, "content": content}}


def rop(path):
    return {"phase": "prepare", "tool": "fs.read", "args": {"path": path}}


def xop(cmd):
    # shell.exec takes argv (list), not a command string.
    return {"phase": "produce", "tool": "shell.exec",
            "args": {"argv": ["sh", "-c", cmd]}}


def wclaim(tid, artifacts):
    return [{"id": f"{tid}-c{i+1}", "phase": "produce",
             "description": f"{a} written", "artifacts": [a],
             "effects": ["create"]} for i, a in enumerate(artifacts)]


def rclaim(tid, artifacts, nreads):
    return [{"id": f"{tid}-c1", "phase": "prepare",
             "description": f"{nreads} input(s) read",
             "artifacts": artifacts, "effects": ["observe"]}]


def xclaim(tid, cmd):
    return [{"id": f"{tid}-c1", "phase": "produce",
             "description": f"command executed: {cmd}", "artifacts": [],
             "effects": ["execute"]}]


def multi(tid, goal, files):
    arts = [f for f, _ in files]
    return write_task(
        tid, goal, arts, ["create"],
        [wop(f, c) for f, c in files], wclaim(tid, arts))


def single(tid, goal, fname, content):
    return write_task(
        tid, goal, [fname], ["create"], [wop(fname, content)],
        wclaim(tid, [fname]))


def readwrite(tid, goal, seeds, outfiles):
    ops = [rop(s) for s, _ in seeds] + [wop(f, c) for f, c in outfiles]
    arts = [f for f, _ in outfiles]
    claims = rclaim(tid, [], len(seeds)) + wclaim(tid, arts)
    return write_task(
        tid, goal, arts, ["create"], ops, claims,
        seed_files={s: c for s, c in seeds})


def readonly(tid, goal, seeds):
    ops = [rop(s) for s, _ in seeds]
    return write_task(
        tid, goal, [], ["observe"], ops, rclaim(tid, [], len(seeds)),
        seed_files={s: c for s, c in seeds})


def exec_task(tid, goal, cmd):
    return write_task(
        tid, goal, [], ["execute"], [xop(cmd)], xclaim(tid, cmd))


# ---------------------------------------------------------------- D1: training
# Goal wording deliberately mixes lexical parallel-triggers ("three",
# "multiple", "several", "each") across production and research tasks:
# the phenomenon under study is the parent's lexical rule vs the
# structural rule the learner must discover.
D1 = [
    multi("d1_01", "Create three report files with the quarterly figures.",
          [("q1.txt", "Q1: 120"), ("q2.txt", "Q2: 140"), ("q3.txt", "Q3: 160")]),
    multi("d1_02", "Generate multiple config files for the staging servers.",
          [("web.cfg", "port=80"), ("db.cfg", "port=5432"),
           ("cache.cfg", "port=6379"), ("queue.cfg", "port=5672")]),
    multi("d1_03", "Write several translation files for the onboarding flow.",
          [("en.txt", "welcome"), ("es.txt", "bienvenido")]),
    single("d1_04", "Write version.txt containing the release number.",
           "version.txt", "2.4.1"),
    single("d1_05", "Create motd.txt with the maintenance notice.",
           "motd.txt", "downtime sunday"),
    readwrite("d1_06", "Read the input log and write a summary file.",
              [("input.txt", "line1\nline2\nline3")],
              [("summary.txt", "3 lines")]),
    readwrite("d1_07", "Combine the two source files into one output.",
              [("a.txt", "aaa"), ("b.txt", "bbb")],
              [("combined.txt", "aaabbb")]),
    readonly("d1_08", "Read the three data files and report their contents.",
             [("data1.txt", "alpha"), ("data2.txt", "beta"),
              ("data3.txt", "gamma")]),
    readonly("d1_09", "Review both reference files.",
             [("ref1.txt", "ref one"), ("ref2.txt", "ref two")]),
    exec_task("d1_10", "Run the environment check command.", "echo env-ok"),
    exec_task("d1_11", "List the working directory contents.", "echo listed"),
    readwrite("d1_12", "Read each changelog entry and split them into notes.",
              [("changes.txt", "v1 v2")],
              [("note1.txt", "v1"), ("note2.txt", "v2")]),
]

# ------------------------------------------------------- S1: sealed pool one
S1 = [
    multi("s1_01", "Produce three invoice files for March.",
          [("inv1.txt", "100"), ("inv2.txt", "200"), ("inv3.txt", "300")]),
    multi("s1_02", "Draft multiple policy documents.",
          [("policy_a.txt", "a"), ("policy_b.txt", "b")]),
    multi("s1_03", "Create four label files for the inventory.",
          [("l1.txt", "1"), ("l2.txt", "2"), ("l3.txt", "3"), ("l4.txt", "4")]),
    single("s1_04", "Write status.txt with the current phase.",
           "status.txt", "phase-two"),
    readwrite("s1_05", "Read the manifest and write the checklist.",
              [("manifest.txt", "m1 m2")], [("checklist.txt", "done")]),
    readonly("s1_06", "Read the three readme files.",
             [("readme1.txt", "r1"), ("readme2.txt", "r2"),
              ("readme3.txt", "r3")]),
    exec_task("s1_07", "Print the current date.", "echo today"),
    readwrite("s1_08", "Read scores and write the top two.",
              [("scores.txt", "s1 s2 s3")],
              [("top1.txt", "s1"), ("top2.txt", "s2")]),
]

# ------------------------------------------------------- D2: confirmation
D2 = [
    multi("d2_01", "Create three backup manifests.",
          [("bak1.txt", "b1"), ("bak2.txt", "b2"), ("bak3.txt", "b3")]),
    multi("d2_02", "Write multiple endpoint stubs for the API.",
          [("e1.txt", "get"), ("e2.txt", "post"),
           ("e3.txt", "put"), ("e4.txt", "del")]),
    multi("d2_03", "Generate two index files.",
          [("idx1.txt", "i1"), ("idx2.txt", "i2")]),
    single("d2_04", "Write build.txt with the build tag.",
           "build.txt", "b-991"),
    single("d2_05", "Create notice.txt with the holiday dates.",
           "notice.txt", "dec 25"),
    readwrite("d2_06", "Read the roster and write the captains list.",
              [("roster.txt", "r1 r2 r3")], [("captains.txt", "r1")]),
    readwrite("d2_07", "Merge the patch notes into one file.",
              [("p1.txt", "p1"), ("p2.txt", "p2")], [("merged.txt", "p1p2")]),
    readonly("d2_08", "Read the three license files.",
             [("lic1.txt", "l1"), ("lic2.txt", "l2"), ("lic3.txt", "l3")]),
    readonly("d2_09", "Inspect the two schema files.",
             [("s1.txt", "schema1"), ("s2.txt", "schema2")]),
    exec_task("d2_10", "Echo the deployment target.", "echo prod"),
    exec_task("d2_11", "Show the disk usage summary.", "echo disk-ok"),
    readwrite("d2_12", "Read feedback and write pro and con files.",
              [("fb.txt", "good bad")],
              [("pro.txt", "good"), ("con.txt", "bad")]),
]

# ------------------------------------------------------- S2: sealed pool two
S2 = [
    multi("s2_01", "Write three release notes.",
          [("rn1.txt", "r1"), ("rn2.txt", "r2"), ("rn3.txt", "r3")]),
    multi("s2_02", "Create several template files.",
          [("t1.txt", "t1"), ("t2.txt", "t2")]),
    single("s2_03", "Write owner.txt with the team name.",
           "owner.txt", "team-red"),
    single("s2_04", "Create ping.txt with the heartbeat.",
           "ping.txt", "alive"),
    readwrite("s2_05", "Read orders and write the packing list.",
              [("orders.txt", "o1 o2")], [("packing.txt", "o1")]),
    readonly("s2_06", "Read the three faq entries.",
             [("faq1.txt", "q1"), ("faq2.txt", "q2"), ("faq3.txt", "q3")]),
    exec_task("s2_07", "Echo the region.", "echo eu-west"),
    readwrite("s2_08", "Read metrics and write high and low files.",
              [("m.txt", "1 9")],
              [("high.txt", "9"), ("low.txt", "1")]),
]

# ------------------------------------------------------- D3: held-out
D3 = [
    multi("d3_01", "Create three credential files for the test accounts.",
          [("c1.txt", "u1"), ("c2.txt", "u2"), ("c3.txt", "u3")]),
    multi("d3_02", "Write multiple locale files.",
          [("de.txt", "de"), ("fr.txt", "fr"),
           ("it.txt", "it"), ("pt.txt", "pt")]),
    multi("d3_03", "Generate two schedule files.",
          [("mon.txt", "m"), ("tue.txt", "t")]),
    single("d3_04", "Write hash.txt with the checksum.",
           "hash.txt", "abc123"),
    single("d3_05", "Create quota.txt with the limit.",
           "quota.txt", "1000"),
    readwrite("d3_06", "Read inventory and write the reorder list.",
              [("inv.txt", "a b c")], [("reorder.txt", "a")]),
    readwrite("d3_07", "Join the chapter files into one.",
              [("ch1.txt", "c1"), ("ch2.txt", "c2")],
              [("book.txt", "c1c2")]),
    readonly("d3_08", "Read the three changelog entries.",
             [("cl1.txt", "v1"), ("cl2.txt", "v2"), ("cl3.txt", "v3")]),
    readonly("d3_09", "Read the authors and contributors files.",
             [("authors.txt", "a"), ("contrib.txt", "c")]),
    exec_task("d3_10", "Echo the service name.", "echo svc"),
    exec_task("d3_11", "Print the uptime marker.", "echo up"),
    readwrite("d3_12", "Read survey and write yes and no tallies.",
              [("survey.txt", "y n y")],
              [("yes.txt", "y y"), ("no.txt", "n")]),
]

SETS = {"d1": D1, "s1": S1, "d2": D2, "s2": S2, "d3": D3}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # Disjointness check: fail loudly, never silently.
    seen: dict[str, str] = {}
    for name, tasks in SETS.items():
        for t in tasks:
            if t["id"] in seen:
                raise RuntimeError(
                    f"task id {t['id']} in both {seen[t['id']]} and {name}")
            seen[t["id"]] = name
            assert "kind" not in t, f"class-C label leaked into {t['id']}"
    sums = []
    for name, tasks in SETS.items():
        payload = {"version": "v2-dseries", "tasks": tasks}
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        path = OUT / f"{name}.json"
        path.write_text(blob + "\n", encoding="utf-8")
        digest = hashlib.sha256(blob.encode()).hexdigest()
        sums.append(f"{digest}  {name}.json")
        print(f"{name}: {len(tasks)} tasks, sha256 {digest[:12]}")
    (OUT / "SHA256SUM").write_text("\n".join(sums) + "\n",
                                   encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
