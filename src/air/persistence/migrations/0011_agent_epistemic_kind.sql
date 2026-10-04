-- 0011_agent_epistemic_kind.sql: first-class epistemic kind on agents.
--
-- An agent's epistemic_kind records whether its effects count as
-- real-world evidence (OBSERVED, the default) or as simulation /
-- forecast / hypothesis / counterfactual (the NON_EVIDENTIARY kinds).
-- The evaluation, assurance, memory, and experience boundaries consult
-- this column to enforce epistemic separation (Invariant #12):
-- simulated work may inform allocation but can never verify reality.
ALTER TABLE agents ADD COLUMN epistemic_kind TEXT NOT NULL DEFAULT 'OBSERVED';
