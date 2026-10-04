-- 0002_capability_effect: capabilities need their effect descriptor persisted.
-- (Promoted capabilities change future allocation via these effects.)

ALTER TABLE capabilities ADD COLUMN effect TEXT;  -- JSON CapabilityEffect
