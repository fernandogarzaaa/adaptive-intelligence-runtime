"""Learning engine (orchestration/policy level).

Learns from experience records — never from raw memory, never from simulated
results. Analysis is statistical aggregation over the comparable dimensions
of OBSERVED experiences. Proposals are candidates with evidence; promotion
goes through the policy gate (independent evaluation + assurance).

What it learns (v1):
- which cognitive strategies succeed, and at what cost (strategy boosts)
- whether spawning is paying off (spawn threshold)
- which agent roles correlate with success (reported, not yet auto-applied)

What it does NOT do: neural training, silent updates, learning from
SIMULATED/FORECAST records, or calling memory storage "learning".
"""

from __future__ import annotations

import json

from air.experience.provenance import LEARNABLE, Provenance
from air.learning.policies import PolicyStore

MIN_EXPERIENCES = 3
POLICY_NAME = "cognitive-allocation"


class LearningEngine:
    def __init__(self, conn, emit=None) -> None:
        self._conn = conn
        self._policies = PolicyStore(conn, emit=emit)
        self._emit = emit

    def analyze(self) -> dict:
        """Aggregate OBSERVED/DERIVED experiences into strategy/role/spawn
        statistics. Returns the analysis with the experience ids behind it."""
        rows = self._conn.execute(
            "SELECT id, dimensions, outcomes, cost, latency_ms FROM experiences"
            " ORDER BY created_at DESC LIMIT 500").fetchall()
        strategies: dict[str, dict] = {}
        roles: dict[str, dict] = {}
        spawns = {"requested": 0, "approved": 0, "denied": 0}
        total_agents_ok, total_agents_fail, n_ok, n_fail = 0, 0, 0, 0
        used_ids: list[str] = []
        for eid, dims_json, outcomes_json, cost, latency in rows:
            dims = json.loads(dims_json or "{}")
            outcomes = json.loads(outcomes_json or "{}")
            # Only learnable provenance. The outcomes of every recorded run
            # are OBSERVED (measured), so this is the normal path; the check
            # protects against future record kinds.
            prov = outcomes.get("provenance", Provenance.OBSERVED.value)
            if Provenance(prov) not in LEARNABLE:
                continue
            used_ids.append(eid)
            ok = dims.get("outcome") == "COMPLETED"
            strat = dims.get("cognitive_strategy", "unknown")
            s = strategies.setdefault(
                strat, {"runs": 0, "successes": 0, "cost": 0.0,
                        "latency_ms": 0})
            s["runs"] += 1
            s["successes"] += 1 if ok else 0
            s["cost"] += cost or 0.0
            s["latency_ms"] += latency or 0
            for role in dims.get("roles", []):
                r = roles.setdefault(role, {"appearances": 0, "successes": 0})
                r["appearances"] += 1
                r["successes"] += 1 if ok else 0
            n = dims.get("agent_count", 0)
            if ok:
                total_agents_ok += n
                n_ok += 1
            else:
                total_agents_fail += n
                n_fail += 1
        for s in strategies.values():
            s["success_rate"] = round(s["successes"] / s["runs"], 3)
            s["avg_cost"] = round(s["cost"] / s["runs"], 4)
            s["avg_latency_ms"] = round(s["latency_ms"] / s["runs"])
        for r in roles.values():
            r["success_rate"] = round(r["successes"] / r["appearances"], 3)
        # Spawn discipline from the event log of the analyzed runs.
        if used_ids:
            q = ("SELECT type, COUNT(*) FROM events WHERE run_id IN"
                 f" ({','.join('?' * len(used_ids))}) AND type LIKE 'spawn.%'"
                 " GROUP BY type")
            for typ, cnt in self._conn.execute(q, used_ids).fetchall():
                key = typ.split(".")[1]
                if key in spawns:
                    spawns[key] = cnt
        return {
            "experiences": len(used_ids),
            "experience_ids": used_ids,
            "strategies": strategies,
            "roles": roles,
            "spawns": spawns,
            "avg_agents_success": round(total_agents_ok / n_ok, 2) if n_ok else 0,
            "avg_agents_failure": round(total_agents_fail / n_fail, 2) if n_fail else 0,
        }

    async def propose_policy_update(self) -> dict | None:
        """Analyze and propose a candidate policy version. Returns the
        proposal (version + evidence) or None when evidence is insufficient.
        Never promotes: promotion needs the human or a gated pipeline."""
        analysis = self.analyze()
        if analysis["experiences"] < MIN_EXPERIENCES:
            return None
        cur = self._policies.current(POLICY_NAME)
        cur_params = dict(cur.changes) if cur else {}
        boosts = dict(cur_params.get("strategy_boosts") or {})
        changes_made: list[str] = []
        # Rule 1: strategies with <50% success over >=2 runs get a negative
        # boost; >80% over >=2 runs get a positive boost. Capped at +/-0.3.
        for strat, s in analysis["strategies"].items():
            if s["runs"] < 2:
                continue
            if s["success_rate"] < 0.5:
                boosts[strat] = round(max(-0.3, boosts.get(strat, 0.0) - 0.1), 3)
                changes_made.append(
                    f"{strat}: success {s['success_rate']} over {s['runs']}"
                    f" runs -> boost {boosts[strat]:+.2f}")
            elif s["success_rate"] > 0.8:
                boosts[strat] = round(min(0.3, boosts.get(strat, 0.0) + 0.1), 3)
                changes_made.append(
                    f"{strat}: success {s['success_rate']} over {s['runs']}"
                    f" runs -> boost {boosts[strat]:+.2f}")
        # Rule 2: if failed runs used MORE agents than successful ones,
        # spawning is not paying off: raise the spawn threshold slightly.
        threshold = float(cur_params.get("spawn_threshold", 0.05))
        if (analysis["avg_agents_failure"] > analysis["avg_agents_success"]
                and analysis["avg_agents_failure"] > 0):
            threshold = round(min(0.5, threshold + 0.05), 3)
            changes_made.append(
                f"failed runs averaged {analysis['avg_agents_failure']} agents"
                f" vs {analysis['avg_agents_success']} for successes"
                f" -> spawn threshold {threshold}")
        if not changes_made:
            return None
        params = {"spawn_threshold": threshold, "strategy_boosts": boosts}
        ver = await self._policies.propose(
            POLICY_NAME, params,
            reason="; ".join(changes_made),
            evidence={"analysis": analysis,
                      "rule": "v1-heuristics",
                      "min_experiences": MIN_EXPERIENCES},
            hypothesis=("Adjusting cognitive-allocation parameters from"
                        " observed strategy/role outcomes will improve"
                        " future run success rates without verified"
                        " regressions."),
            expected_effect={"direction": "improve",
                             "metrics": ["success_rate", "verified_rate"]},
            constraints={"min_verified_rate": "no regression vs baseline",
                         "max_cost_ratio": 1.25},
            generated_by="air-learning-engine v1",
            source_experiences=analysis["experience_ids"])
        if self._emit:
            await self._emit("learning.proposed",
                             payload={"policy": POLICY_NAME,
                                      "version": ver.version,
                                      "changes": changes_made})
        return {"policy": POLICY_NAME, "version": ver.version,
                "changes": changes_made, "evidence_experiences":
                analysis["experience_ids"]}
