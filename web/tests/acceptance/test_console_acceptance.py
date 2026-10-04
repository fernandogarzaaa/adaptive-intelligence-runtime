#!/usr/bin/env python3
"""AIR Console acceptance: the full operator scenario through the REAL UI
against a LIVE backend. No mocks, no fabricated state: the only scripted
pieces are the agent behaviors registered by harness.py (the sandbox has no
LLM provider), documented there.

Run from the repo root:
    /usr/bin/python3 -m pytest web/tests/acceptance/test_console_acceptance.py -v

Requires web/dist to be built (npm run build in web/).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

REPO = Path(__file__).resolve().parents[3]
HARNESS = REPO / "web" / "tests" / "acceptance" / "harness.py"
VENV_PY = Path.home() / ".venvs" / "air-runtime" / "bin" / "python"
PORT = 18765


def api_get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=15) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def server():
    dist = REPO / "web" / "dist"
    assert dist.is_dir(), "web/dist missing: run 'npm run build' in web/ first"
    data_dir = Path(tempfile.mkdtemp(prefix="air-console-accept-"))
    proc = subprocess.Popen(
        [str(VENV_PY), str(HARNESS), "--port", str(PORT),
         "--data-dir", str(data_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{PORT}"
    deadline = time.time() + 60
    ready = False
    while time.time() < deadline:
        line = proc.stdout.readline() if proc.stdout else ""
        if "ACCEPTANCE_READY" in line:
            ready = True
            break
        if proc.poll() is not None:
            break
    assert ready, "harness did not start"
    for _ in range(50):
        try:
            assert api_get(base, "/health")["ok"] is True
            break
        except Exception:
            time.sleep(0.2)
    yield {"base": base, "data_dir": data_dir}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def page(server):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    pg = ctx.new_page()
    pg.goto(server["base"])
    expect(pg.get_by_role("heading", name="AIR Console")).to_be_visible(timeout=15000)
    yield pg
    ctx.close()
    browser.close()
    pw.stop()


def step(n: str):
    print(f"\n=== {n} ===", flush=True)


def api_post(page: Page, path: str, body: dict):
    """POST through the page (same origin): returns (status, json)."""
    return page.evaluate(
        """async ([path, body]) => {
             const r = await fetch(path, {method: 'POST',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify(body)});
             let j = null; try { j = await r.json(); } catch (e) {}
             return [r.status, j];
           }""", [path, body])


def wait_for_text(page: Page, text: str, timeout: int = 30000):
    expect(page.get_by_text(text).first).to_be_visible(timeout=timeout)


def spawn_panel(page: Page):
    return page.locator(".panel", has=page.locator(".panel-h", has_text="Spawn decisions"))


def create_run_ui(page: Page, base: str, goal: str, strategy: str = "single_agent",
                  budget: str | None = None) -> str:
    page.goto(base + "/runs")
    page.get_by_role("button", name="New run").click()
    page.locator("#run-goal").fill(goal)
    page.locator("#run-strategy").select_option(strategy)
    if budget is not None:
        page.locator("#run-budget").fill(budget)
    page.get_by_role("button", name="Create and open live view").click()
    page.wait_for_url("**/runs/*/live", timeout=15000)
    return page.url.rstrip("/").split("/")[-2]


# ---------------------------------------------------------------- scenario

def test_console_acceptance(page: Page, server):
    base = server["base"]
    data_dir: Path = server["data_dir"]
    state: dict = {}

    # -- 1. console loads, honest empty state ---------------------------
    step("1. console loads with honest NO MODEL PROVIDER panel")
    page.goto(base + "/runs")
    wait_for_text(page, "Model execution unavailable")

    # -- 2. create run through the UI ------------------------------------
    step("2. create run via UI (single_agent)")
    run1 = create_run_ui(page, base, "Audit this repository")
    state["run1"] = run1
    print("run1:", run1)
    wait_for_text(page, "cognitive-allocation@v1")
    wait_for_text(page, "single_agent")

    # -- 3. graph updates live, run completes -----------------------------
    step("3. live graph populates, run completes")
    graph = page.locator(".panel", has=page.locator(".panel-h", has_text="Cognitive graph"))
    expect(graph.get_by_text("specialist")).to_be_visible(timeout=30000)
    expect(page.get_by_text("COMPLETED").first).to_be_visible(timeout=60000)
    print("run1 completed")

    # -- 4. spawn sibling through the UI ----------------------------------
    step("4. spawn sibling via UI, graph updates")
    graph.get_by_text("specialist").click()
    page.get_by_role("button", name="Spawn child").click()
    page.locator(".dialog input.inp").fill("researcher")
    page.locator(".dialog textarea.inp").fill("gather context")
    page.get_by_role("button", name="Request spawn").click()
    wait_for_text(page, "decision", timeout=15000)
    page.get_by_role("button", name="Done").click()
    expect(graph.get_by_text("researcher")).to_be_visible(timeout=15000)
    print("sibling spawned, graph shows researcher")

    # -- 5. spawn decision WHY panel --------------------------------------
    step("5. spawn decision WHY panel (approved)")
    spawn_panel(page).scroll_into_view_if_needed()
    spawn_panel(page).locator("tbody tr").first.locator("button").click()
    wait_for_text(page, "WHY: spawn decision", timeout=15000)
    wait_for_text(page, "single_agent")
    page.get_by_role("button", name="Close").click()
    print("WHY panel shows strategy and decision evidence")

    # -- 6. tool call authorization + provenance --------------------------
    step("6. tool authorization decision + provenance in UI")
    agents = api_get(base, f"/runs/{run1}/agents")
    specialist = next(a for a in agents if a["role"] == "specialist")
    state["specialist"] = specialist["id"]
    page.goto(base + f"/runs/{run1}")
    page.get_by_role("button", name="Tools").click()
    wait_for_text(page, "fs.read", timeout=15000)
    wait_for_text(page, "COMMITTED")
    page.get_by_text("fs.read").first.click()
    wait_for_text(page, "GRANT", timeout=15000)
    wait_for_text(page, "untrusted", timeout=15000)
    # the injection string is displayed as framed data, never executed
    wait_for_text(page, "ignore all previous instructions")
    print("tool call GRANT + untrusted provenance visible")

    # -- 7. timeline shows the canonical event chain -----------------------
    step("7. timeline shows tool event chain")
    page.get_by_role("button", name="Timeline").click()
    for ev in ("tool.requested", "tool.validated", "tool.authorized",
               "tool.dispatched", "tool.completed"):
        wait_for_text(page, ev, timeout=15000)
    print("canonical chain visible")

    # -- 8. experience recorded --------------------------------------------
    step("8. experience appears")
    page.get_by_role("button", name="Experience").click()
    wait_for_text(page, "specialist", timeout=15000)

    # -- 9. evaluation -> SUPPORTED -----------------------------------------
    step("9. run evaluation via UI")
    page.get_by_role("button", name="Evaluation").click()
    page.get_by_role("button", name="Run evaluation").click()
    wait_for_text(page, "SUPPORTED", timeout=30000)
    eval_id = api_get(base, f"/runs/{run1}/evaluations")[-1]["id"]
    state["eval_id"] = eval_id
    print("evaluation:", eval_id, "SUPPORTED")

    # -- 10. assurance -> SOUND ----------------------------------------------
    step("10. run assurance via UI")
    page.get_by_role("button", name="Assurance").click()
    page.get_by_role("button", name="Run assurance").click()
    wait_for_text(page, "SOUND", timeout=30000)
    asr_id = api_get(base, f"/runs/{run1}/assurance")[-1]["id"]
    state["asr_id"] = asr_id
    print("assurance:", asr_id, "SOUND")

    # -- 11. propose policy v2 -----------------------------------------------
    step("11. propose policy v2 via UI")
    page.goto(base + "/policies/cognitive-allocation")
    page.get_by_role("button", name="Propose").click()
    page.locator("#pd-params").fill(json.dumps({"spawn_threshold": 0.04}))
    page.locator("#pd-reason").fill(
        "acceptance: lower spawn threshold after verified single_agent run")
    page.get_by_role("button", name="Propose version").click()
    wait_for_text(page, "v2", timeout=15000)
    print("v2 proposed")

    # -- 12. promote v2 with real evidence ------------------------------------
    step("12. promote v2 via UI with real evaluation+assurance")
    page.get_by_role("button", name="Promote").click()
    page.locator("#pd-m-ver").fill("v2")
    page.locator("#pd-m-eval").fill(eval_id)
    page.locator("#pd-m-ass").fill(asr_id)
    page.get_by_role("button", name="Promote", exact=True).click()
    page.wait_for_timeout(2000)
    prov = api_get(base, "/policies/cognitive-allocation/provenance")
    assert prov["active_version"] == "v2", prov
    print("v2 promoted, active_version =", prov["active_version"])

    # -- 13. new run uses the promoted policy ----------------------------------
    step("13. new run header shows cognitive-allocation@v2")
    run2 = create_run_ui(page, base, "Second audit under v2")
    state["run2"] = run2
    wait_for_text(page, "cognitive-allocation@v2", timeout=15000)
    expect(page.get_by_text("COMPLETED").first).to_be_visible(timeout=60000)
    print("run2:", run2, "under v2, completed")

    # -- 14. attacks: permission escalation ------------------------------------
    step("14. attack: permission escalation (READ agent -> fs.write)")
    status, body = api_post(
        page, f"/agents/{specialist['id']}/tools/call",
        {"tool_name": "fs.write",
         "args": {"path": "pwned.txt", "content": "x"}})
    assert status == 403, (status, body)
    print("escalation blocked:", status)
    page.goto(base + "/authority")
    page.locator("#auth-state").select_option("DENIED")
    wait_for_text(page, "fs.write", timeout=15000)
    page.get_by_text("fs.write").first.click()
    wait_for_text(page, "capability READ not granted", timeout=15000)
    print("real denial with resolver checks visible in Authority")

    # -- 15. attacks: cross-run message -----------------------------------------
    step("15. attack: cross-run message")
    agents2 = api_get(base, f"/runs/{run2}/agents")
    other = agents2[0]["id"]
    status, body = page.evaluate(
        """async ([a, b, run]) => {
             const r = await fetch(`/agents/${a}/message?run_id=${run}`, {
               method: 'POST', headers: {'Content-Type': 'application/json'},
               body: JSON.stringify({to_agent_id: b, channel: 'sibling',
                                     kind: 'note', payload: {x: 1}})});
             return [r.status, await r.json().catch(() => null)];
           }""", [specialist["id"], other, run1])
    assert status == 403, (status, body)
    print("cross-run message blocked:", status)
    page.goto(base + f"/runs/{run1}")
    page.get_by_role("button", name="Timeline").click()
    wait_for_text(page, "policy.blocked", timeout=15000)
    print("policy.blocked event visible in timeline")

    # -- 16. attacks: budget exhaustion -------------------------------------------
    step("16. attack: budget exhaustion via UI")
    run3 = create_run_ui(page, base, "budget exhaustion probe", budget="1")
    state["run3"] = run3
    expect(page.get_by_text("COMPLETED").first).to_be_visible(timeout=60000)
    graph3 = page.locator(".panel", has=page.locator(".panel-h", has_text="Cognitive graph"))
    for i in range(2):
        graph3.get_by_text("specialist").click()
        page.get_by_role("button", name="Spawn child").click()
        page.locator(".dialog input.inp").fill(f"extra-{i}")
        page.locator(".dialog textarea.inp").fill("budget probe")
        page.get_by_role("button", name="Request spawn").click()
        page.wait_for_timeout(2000)
        # dialog shows the decision; close it either way
        if page.get_by_role("button", name="Done").is_visible():
            page.get_by_role("button", name="Done").click()
        else:
            page.get_by_role("button", name="Cancel").click()
    spawn_panel(page).scroll_into_view_if_needed()
    wait_for_text(page, "DENY", timeout=15000)
    print("budget denial visible in spawn decisions")

    # -- 17. attacks: evaluator spoofing --------------------------------------------
    step("17. attack: evaluator spoofing (fabricated verdicts)")
    status, body = api_post(
        page, "/policies/cognitive-allocation/promote",
        {"version": "v9",
         "evaluation": {"verdict": "SUPPORTED"},
         "assurance": {"evaluator_verdict": "SOUND",
                       "system_verdict": "SUPPORTED"}})
    assert status == 422, (status, body)
    print("spoofed verdicts rejected:", status)

    # -- 18. regression under v2 ------------------------------------------------------
    step("18. introduce regression under v2")
    (data_dir / "regression.on").write_text("on")
    run4 = create_run_ui(page, base, "regression probe under v2")
    state["run4"] = run4
    wait_for_text(page, "FAILED", timeout=60000)
    print("run4:", run4, "agent FAILED under v2")
    (data_dir / "regression.on").unlink()

    # -- 19. attacks: promotion bypass (weak evidence) -------------------------------
    step("19. attack: promotion bypass with non-SUPPORTED evaluation")
    page.goto(base + f"/runs/{run4}")
    page.get_by_role("button", name="Evaluation").click()
    page.get_by_role("button", name="Run evaluation").click()
    page.wait_for_timeout(3000)
    weak = api_get(base, f"/runs/{run4}/evaluations")
    assert weak, "expected an evaluation on run4"
    weak_id = weak[-1]["id"]
    print("regression evaluation:", weak_id)
    status, body = api_post(
        page, "/policies/cognitive-allocation/promote",
        {"version": "v3", "evaluation_id": weak_id,
         "assurance_id": state["asr_id"]})
    assert status in (404, 409), (status, body)
    print("bypass blocked:", status, str(body)[:160] if body else "")
    prov = api_get(base, "/policies/cognitive-allocation/provenance")
    assert prov["active_version"] == "v2", prov

    # -- 20. rollback request + approve through the UI ---------------------------------
    step("20. rollback request + approve via UI")
    page.goto(base + "/policies/cognitive-allocation")
    page.get_by_role("button", name="Rollback").click()
    page.get_by_role("button", name="Request rollback").click()
    wait_for_text(page, "rollback target (TO)", timeout=15000)
    page.locator("#pd-rb-reason").fill(
        f"acceptance: agent failures in run {run4[:12]} under v2")
    page.get_by_role("button", name="Record rollback request").click()
    wait_for_text(page, "Rollback request recorded", timeout=15000)
    page.get_by_role("button", name="Approve rollback").click()
    page.wait_for_timeout(2000)
    prov = api_get(base, "/policies/cognitive-allocation/provenance")
    assert prov["active_version"] == "v1", prov
    print("rolled back, active_version =", prov["active_version"])

    # -- 21. reload: state correct -------------------------------------------------------
    step("21. browser reload, state converges")
    run5 = create_run_ui(page, base, "post-rollback sanity")
    page.reload()
    wait_for_text(page, "cognitive-allocation@v1", timeout=15000)
    expect(page.get_by_text("COMPLETED").first).to_be_visible(timeout=60000)
    print("reload converges on v1")

    # -- 22. offline / reconnect replay -----------------------------------------------------
    step("22. transport disconnect, reconnect, replay converges")
    ctx = page.context
    before = api_get(base, "/runs?limit=100")
    n_before = len(before)
    ctx.set_offline(True)
    page.wait_for_timeout(1500)
    req = urllib.request.Request(
        base + "/runs", data=json.dumps(
            {"goal": "offline probe", "strategy": "single_agent"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        assert r.status in (200, 201)
    ctx.set_offline(False)
    page.goto(base + "/runs")
    page.wait_for_function(
        "document.body.innerText.includes('offline probe')", timeout=30000)
    after = api_get(base, "/runs?limit=100")
    assert len(after) == n_before + 1, (n_before, len(after))
    print("reconnect replayed, UI converged on", len(after), "runs")

    print("\nACCEPTANCE COMPLETE")
