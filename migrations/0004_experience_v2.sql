-- 0004_experience_v2: comparable dimensions + evaluation/assurance refs.
-- dimensions is a structured JSON object with the comparable dimensions the
-- learning layer aggregates over (strategy, topology, roles, models, tools,
-- capabilities, budget, verification, outcome, ...).

ALTER TABLE experiences ADD COLUMN dimensions TEXT;
ALTER TABLE experiences ADD COLUMN evaluation_refs TEXT;
ALTER TABLE experiences ADD COLUMN assurance_refs TEXT;
ALTER TABLE experiences ADD COLUMN runtime_version TEXT;
ALTER TABLE experiences ADD COLUMN environment_version TEXT;
ALTER TABLE experiences ADD COLUMN policy_versions TEXT;
ALTER TABLE experiences ADD COLUMN capability_versions TEXT;
