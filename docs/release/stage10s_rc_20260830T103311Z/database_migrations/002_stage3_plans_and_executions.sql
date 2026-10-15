-- RescueSwarm Stage 3 PostgreSQL/PostGIS-compatible additions.
-- Apply only after 001_stage2_postgresql.sql. JSONB keeps planner data exact and
-- storage-neutral; spatial indexing can be added when offline map datasets arrive.

CREATE TABLE IF NOT EXISTS mission_plans (
    mission_id UUID PRIMARY KEY REFERENCES missions(id) ON DELETE CASCADE,
    planner_version VARCHAR(80) NOT NULL,
    plan_fingerprint VARCHAR(64) NOT NULL,
    mission_fingerprint VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mission_plans_plan_fingerprint ON mission_plans(plan_fingerprint);
CREATE INDEX IF NOT EXISTS ix_mission_plans_mission_fingerprint ON mission_plans(mission_fingerprint);

CREATE TABLE IF NOT EXISTS mission_executions (
    mission_id UUID PRIMARY KEY REFERENCES missions(id) ON DELETE CASCADE,
    source VARCHAR(20) NOT NULL,
    state VARCHAR(30) NOT NULL,
    mission_fingerprint VARCHAR(64),
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mission_executions_source ON mission_executions(source);
CREATE INDEX IF NOT EXISTS ix_mission_executions_state ON mission_executions(state);
