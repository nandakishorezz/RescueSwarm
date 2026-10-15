BEGIN;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS missions (
  id varchar(36) PRIMARY KEY,
  name varchar(120) NOT NULL,
  state varchar(20) NOT NULL,
  created_at timestamptz NOT NULL,
  started_at timestamptz,
  completed_at timestamptz,
  area_m2 double precision,
  elapsed_seconds double precision NOT NULL DEFAULT 0,
  coverage_percent double precision NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_missions_state ON missions(state);

CREATE TABLE IF NOT EXISTS mission_boundaries (
  mission_id varchar(36) PRIMARY KEY REFERENCES missions(id),
  geometry_wgs84 jsonb NOT NULL,
  geom geometry(Polygon, 4326),
  source_format varchar(20) NOT NULL,
  area_m2 double precision NOT NULL,
  area_hectares double precision NOT NULL,
  origin jsonb NOT NULL,
  warnings jsonb NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_mission_boundaries_geom ON mission_boundaries USING gist(geom);

CREATE TABLE IF NOT EXISTS vehicles (
  mission_id varchar(36) REFERENCES missions(id),
  vehicle_id varchar(80),
  name varchar(120) NOT NULL,
  state jsonb NOT NULL,
  last_update timestamptz NOT NULL,
  PRIMARY KEY (mission_id, vehicle_id)
);

CREATE TABLE IF NOT EXISTS telemetry (
  id bigserial PRIMARY KEY,
  mission_id varchar(36) REFERENCES missions(id),
  vehicle_id varchar(80) NOT NULL,
  timestamp timestamptz NOT NULL,
  sequence integer NOT NULL,
  latitude double precision NOT NULL,
  longitude double precision NOT NULL,
  relative_altitude_m double precision NOT NULL,
  battery_percent double precision NOT NULL,
  payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_telemetry_mission_vehicle_time ON telemetry(mission_id, vehicle_id, timestamp);

CREATE TABLE IF NOT EXISTS targets (
  target_id varchar(36) PRIMARY KEY,
  mission_id varchar(36) REFERENCES missions(id),
  state varchar(20) NOT NULL,
  delivery_state varchar(20) NOT NULL,
  data jsonb NOT NULL,
  last_seen_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_targets_mission ON targets(mission_id);

CREATE TABLE IF NOT EXISTS deliveries (
  delivery_id varchar(36) PRIMARY KEY,
  mission_id varchar(36) REFERENCES missions(id),
  target_id varchar(36) REFERENCES targets(target_id),
  vehicle_id varchar(80),
  state varchar(20) NOT NULL,
  simulated boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  event_id varchar(36) PRIMARY KEY,
  mission_id varchar(36) REFERENCES missions(id),
  vehicle_id varchar(80),
  target_id varchar(36),
  type varchar(40) NOT NULL,
  severity varchar(20) NOT NULL,
  timestamp timestamptz NOT NULL,
  message varchar(500) NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  simulated boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_events_mission_time ON events(mission_id, timestamp);
COMMIT;

