-- Drop broken view: references t.amount (renamed to amount_cents in 004), never queried from Python.
DROP VIEW IF EXISTS v_finance_monthly_spend;

-- Composite index for timeline queries that filter by sensitivity.
CREATE INDEX IF NOT EXISTS idx_events_occurred_sensitivity
ON events(occurred_at, sensitivity);
