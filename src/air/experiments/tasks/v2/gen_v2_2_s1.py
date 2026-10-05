"""Generate the v2.2 S1 sealed pool: 60 independent stratified tasks.

Strata (preregistered, evaluation-only labels; candidate does not see these):
- production (30): multi-unit file creation with lexical parallel-triggers.
  The parent's lexical rule fires on "three"/"multiple"/"several"/"each".
  The learned rule should override with single_agent (requires WRITE).
- research (20): read-only tasks with lexical triggers.
  Both policies should choose parallel_agents (requires OBSERVE).
- mixed (10): read-then-write tasks. Tests generalization.

All 60 are independently specified: unique IDs, filenames, contents,
goal wordings. No duplication of S1-pilot tasks.

Deterministic output. Run: python3 gen_v2_2_s1.py
Writes: s1_v2_2.json + updates SHA256SUM
"""

import hashlib
import json
from pathlib import Path

OUT = Path(__file__).parent


def write_task(tid, goal, artifacts, effects, operations, claims,
               seed_files=None, stratum=None):
    d = {
        "id": tid,
        "goal": goal,
        "artifacts": artifacts,
        "effects": effects,
        "operations": operations,
        "claims": claims,
        "seed_files": seed_files or {},
    }
    if stratum:
        d["stratum"] = stratum  # evaluation-only label
    return d


def wop(path, content):
    return {"phase": "produce", "tool": "fs.write",
            "args": {"path": path, "content": content}}


def rop(path):
    return {"phase": "prepare", "tool": "fs.read", "args": {"path": path}}


def wclaim(tid, artifacts):
    return [{"id": f"{tid}-c{i+1}", "phase": "produce",
             "description": f"{a} written", "artifacts": [a],
             "effects": ["create"]} for i, a in enumerate(artifacts)]


def rclaim(tid, nreads):
    return [{"id": f"{tid}-c1", "phase": "prepare",
             "description": f"{nreads} input(s) read",
             "artifacts": [], "effects": ["observe"]}]


# Lexical triggers that fire the parent's parallel_agents rule.
TRIGGERS = ["three", "multiple", "several", "each of the", "various"]

# Thematic file groups for independent production tasks.
# Each entry: (theme, [(filename, content), ...])
PRODUCTION_GROUPS = [
    ("invoice", [("inv_mar.txt", "March: 4500"), ("inv_apr.txt", "April: 5200"), ("inv_may.txt", "May: 4800")]),
    ("policy", [("pol_a.txt", "Policy A: remote work"), ("pol_b.txt", "Policy B: leave")]),
    ("label", [("lbl_1.txt", "SKU-001"), ("lbl_2.txt", "SKU-002"), ("lbl_3.txt", "SKU-003"), ("lbl_4.txt", "SKU-004")]),
    ("report", [("rpt_q1.txt", "Q1 revenue 1.2M"), ("rpt_q2.txt", "Q2 revenue 1.5M")]),
    ("config", [("cfg_web.txt", "host=web1"), ("cfg_db.txt", "host=db1"), ("cfg_cache.txt", "host=cache1")]),
    ("translation", [("tr_en.txt", "Hello"), ("tr_fr.txt", "Bonjour"), ("tr_de.txt", "Hallo")]),
    ("backup", [("bkp_01.txt", "snapshot a"), ("bkp_02.txt", "snapshot b")]),
    ("endpoint", [("ep_get.txt", "GET /users"), ("ep_post.txt", "POST /users"), ("ep_del.txt", "DELETE /users")]),
    ("changelog", [("cl_v1.txt", "v1.0 released"), ("cl_v2.txt", "v2.0 released")]),
    ("manifest", [("mft_a.txt", "item A"), ("mft_b.txt", "item B"), ("mft_c.txt", "item C")]),
    ("certificate", [("cert_01.txt", "CN=alpha"), ("cert_02.txt", "CN=beta")]),
    ("schedule", [("sch_mon.txt", "Monday: deploy"), ("sch_tue.txt", "Tuesday: test"), ("sch_wed.txt", "Wednesday: review")]),
    ("receipt", [("rcp_001.txt", "$12.50"), ("rcp_002.txt", "$8.75"), ("rcp_003.txt", "$22.00")]),
    ("license", [("lic_mit.txt", "MIT License"), ("lic_apl.txt", "Apache License")]),
    ("template", [("tpl_hdr.txt", "<header>"), ("tpl_ftr.txt", "<footer>"), ("tpl_nav.txt", "<nav>")]),
    ("logarch", [("log_01.txt", "INFO start"), ("log_02.txt", "INFO stop")]),
    ("metric", [("met_cpu.txt", "cpu: 45%"), ("met_mem.txt", "mem: 62%"), ("met_dsk.txt", "dsk: 78%")]),
    ("user", [("usr_01.txt", "alice"), ("usr_02.txt", "bob")]),
    ("order", [("ord_101.txt", "order 101"), ("ord_102.txt", "order 102"), ("ord_103.txt", "order 103")]),
    ("ticket", [("tkt_11.txt", "bug: login"), ("tkt_12.txt", "bug: search")]),
    ("spec", [("spc_api.txt", "API v2"), ("spc_ui.txt", "UI v3"), ("spc_db.txt", "DB v1")]),
    ("note", [("nte_1.txt", "meeting notes"), ("nte_2.txt", "call notes")]),
    ("draft", [("drf_a.txt", "draft A"), ("drf_b.txt", "draft B"), ("drf_c.txt", "draft C")]),
    ("summary", [("sum_w1.txt", "week 1"), ("sum_w2.txt", "week 2")]),
    ("plan", [("pln_q3.txt", "Q3 goals"), ("pln_q4.txt", "Q4 goals"), ("pln_q1.txt", "Q1 goals")]),
    ("audit", [("adt_01.txt", "audit trail 1"), ("adt_02.txt", "audit trail 2")]),
    ("export", [("exp_csv.txt", "a,b,c"), ("exp_json.txt", '{"x": 1}')]),
    ("import", [("imp_01.txt", "import batch 1"), ("imp_02.txt", "import batch 2"), ("imp_03.txt", "import batch 3")]),
    ("deploy", [("dpl_stg.txt", "staging OK"), ("dpl_prd.txt", "prod OK")]),
    ("test", [("tst_u1.txt", "unit 1 pass"), ("tst_u2.txt", "unit 2 pass")]),
]

# Research groups: (theme, [(seed_file, content), ...])
RESEARCH_GROUPS = [
    ("readme", [("rdm_1.txt", "readme one"), ("rdm_2.txt", "readme two"), ("rdm_3.txt", "readme three")]),
    ("data", [("dat_a.txt", "alpha"), ("dat_b.txt", "beta")]),
    ("ref", [("ref_x1.txt", "reference X1"), ("ref_x2.txt", "reference X2")]),
    ("manual", [("man_01.txt", "chapter 1"), ("man_02.txt", "chapter 2"), ("man_03.txt", "chapter 3")]),
    ("guide", [("gde_a.txt", "guide A"), ("gde_b.txt", "guide B")]),
    ("doc", [("doc_1.txt", "doc one"), ("doc_2.txt", "doc two")]),
    ("spec_read", [("spcr_1.txt", "spec read 1"), ("spcr_2.txt", "spec read 2"), ("spcr_3.txt", "spec read 3")]),
    ("log_read", [("lgr_01.txt", "log entry 1"), ("lgr_02.txt", "log entry 2")]),
    ("config_read", [("cfgr_a.txt", "config A"), ("cfgr_b.txt", "config B")]),
    ("report_read", [("rptr_1.txt", "report 1"), ("rptr_2.txt", "report 2")]),
    ("memo", [("mem_01.txt", "memo one"), ("mem_02.txt", "memo two"), ("mem_03.txt", "memo three")]),
    ("brief", [("brf_a.txt", "brief A"), ("brf_b.txt", "brief B")]),
    ("analysis", [("anl_1.txt", "analysis 1"), ("anl_2.txt", "analysis 2")]),
    ("review", [("rev_01.txt", "review 1"), ("rev_02.txt", "review 2"), ("rev_03.txt", "review 3")]),
    ("survey", [("srv_a.txt", "survey A"), ("srv_b.txt", "survey B")]),
    ("feedback", [("fbk_1.txt", "feedback 1"), ("fbk_2.txt", "feedback 2")]),
    ("record", [("rec_01.txt", "record 1"), ("rec_02.txt", "record 2"), ("rec_03.txt", "record 3")]),
    ("transcript", [("trn_a.txt", "transcript A"), ("trn_b.txt", "transcript B")]),
    ("minutes", [("min_1.txt", "minutes 1"), ("min_2.txt", "minutes 2")]),
    ("ledger_read", [("ldg_01.txt", "ledger 1"), ("ldg_02.txt", "ledger 2"), ("ldg_03.txt", "ledger 3")]),
]

# Mixed groups: (theme, seeds, outputs)
MIXED_GROUPS = [
    ("manifest_check", [("mfx_in.txt", "m1 m2 m3")], [("mfx_out.txt", "checked: 3")]),
    ("score_top", [("scx_in.txt", "s1 s2 s3 s4")], [("scx_t1.txt", "s1"), ("scx_t2.txt", "s2")]),
    ("log_summary", [("lgx_in.txt", "e1 e2")], [("lgx_sum.txt", "2 events")]),
    ("data_split", [("dtx_in.txt", "a b c")], [("dtx_1.txt", "a"), ("dtx_2.txt", "b"), ("dtx_3.txt", "c")]),
    ("changelog_notes", [("chx_in.txt", "v1 v2 v3")], [("chx_n1.txt", "v1"), ("chx_n2.txt", "v2")]),
    ("inventory_count", [("ivx_in.txt", "i1 i2 i3 i4")], [("ivx_cnt.txt", "4 items")]),
    ("config_merge", [("cfx_a.txt", "a=1"), ("cfx_b.txt", "b=2")], [("cfx_out.txt", "a=1 b=2")]),
    ("report_combine", [("rpx_1.txt", "part 1"), ("rpx_2.txt", "part 2")], [("rpx_full.txt", "part 1 part 2")]),
    ("user_filter", [("usx_in.txt", "alice bob carol")], [("usx_out.txt", "alice, carol")]),
    ("metric_avg", [("mex_1.txt", "10"), ("mex_2.txt", "20"), ("mex_3.txt", "30")], [("mex_avg.txt", "20")]),
]


def main():
    tasks = []

    # Production: 30 tasks
    for i, (theme, files) in enumerate(PRODUCTION_GROUPS):
        tid = f"s1p_{i+1:02d}"
        trigger = TRIGGERS[i % len(TRIGGERS)]
        n = len(files)
        goal = f"Create {trigger} {theme} files for the {theme} archive."
        arts = [f for f, _ in files]
        ops = [wop(f, c) for f, c in files]
        tasks.append(write_task(
            tid, goal, arts, ["create"], ops, wclaim(tid, arts),
            stratum="production"))

    # Research: 20 tasks
    for i, (theme, seeds) in enumerate(RESEARCH_GROUPS):
        tid = f"s1r_{i+1:02d}"
        trigger = TRIGGERS[i % len(TRIGGERS)]
        n = len(seeds)
        goal = f"Read {trigger} {theme} files and report their contents."
        ops = [rop(s) for s, _ in seeds]
        tasks.append(write_task(
            tid, goal, [], ["observe"], ops, rclaim(tid, n),
            seed_files={s: c for s, c in seeds},
            stratum="research"))

    # Mixed: 10 tasks
    for i, (theme, seeds, outfiles) in enumerate(MIXED_GROUPS):
        tid = f"s1m_{i+1:02d}"
        goal = f"Process the {theme} input and write the result."
        ops = [rop(s) for s, _ in seeds] + [wop(f, c) for f, c in outfiles]
        arts = [f for f, _ in outfiles]
        claims = rclaim(tid, len(seeds)) + wclaim(tid, arts)
        tasks.append(write_task(
            tid, goal, arts, ["create"], ops, claims,
            seed_files={s: c for s, c in seeds},
            stratum="mixed"))

    assert len(tasks) == 60, f"expected 60, got {len(tasks)}"
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "duplicate IDs"

    # Verify strata counts
    from collections import Counter
    c = Counter(t["stratum"] for t in tasks)
    assert c["production"] == 30, c
    assert c["research"] == 20, c
    assert c["mixed"] == 10, c

    # Verify disjoint from all existing task IDs
    existing = set()
    for fn in ["d1.json", "s1.json", "d2.json", "s2.json", "d3.json"]:
        p = OUT / fn
        if p.exists():
            data = json.loads(p.read_text())
            tl = data["tasks"] if isinstance(data, dict) else data
            existing.update(t["id"] for t in tl)
    overlap = set(ids) & existing
    assert not overlap, f"overlap with existing: {overlap}"

    out_data = {"version": "v2.2", "strata": {"production": 30, "research": 20, "mixed": 10}, "tasks": tasks}
    out_path = OUT / "s1_v2_2.json"
    out_path.write_text(json.dumps(out_data, indent=2) + "\n")

    # Hash
    h = hashlib.sha256(out_path.read_bytes()).hexdigest()
    print(f"s1_v2_2.json: {len(tasks)} tasks, sha256={h[:16]}...")
    print(f"  production: {c['production']}, research: {c['research']}, mixed: {c['mixed']}")
    return h


if __name__ == "__main__":
    main()
