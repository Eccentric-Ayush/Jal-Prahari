-- =============================================================================
-- Jal-Prahari — Raw SQL Migrations
-- =============================================================================
-- Purpose:  PostgreSQL DDL equivalent of the SQLAlchemy ORM models.
--           Use this file when you need to:
--             • Inspect the schema without running Python.
--             • Apply migrations via a DBA tool (pgAdmin, DBeaver, flyway).
--             • Bootstrap a CI database in a pipeline that doesn't run Python.
--             • Audit the schema independently of application code.
--
-- Execution order matters — run statements top to bottom.
-- All statements use IF NOT EXISTS to be safely re-runnable (idempotent).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- STEP 1: Enable PostGIS extension
-- -----------------------------------------------------------------------------
-- PostGIS adds spatial types (geometry, geography), spatial functions (ST_*),
-- and spatial index support (GiST) to PostgreSQL.
-- Must be created before any column uses the `geometry` data type.
-- Requires SUPERUSER or the CREATE privilege on the current database.
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS postgis;

-- Verify:
-- SELECT PostGIS_full_version();


-- -----------------------------------------------------------------------------
-- STEP 2: Create the `sensors` table
-- -----------------------------------------------------------------------------
-- Each row represents one physical IoT sensor installed in the city.
--
-- Column notes:
--   id             — SERIAL surrogate key; auto-incremented by the sequence.
--   name           — Human-readable, globally unique sensor label.
--   geometry       — PostGIS POINT in WGS84 (SRID 4326). Stores lon/lat.
--   base_elevation — Altitude (metres above MSL) from DEM; nullable until
--                    the DEM pipeline populates it.
--   created_at     — Server-side UTC timestamp; TIMESTAMPTZ stores the timezone
--                    offset so comparisons across TZs are always correct.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sensors (
    id              SERIAL          PRIMARY KEY,
    name            VARCHAR(255)    NOT NULL,
    geometry        GEOMETRY(POINT, 4326)   NOT NULL,
    base_elevation  FLOAT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Enforce name uniqueness at the database level.
    -- A UNIQUE constraint implicitly creates a B-tree index on `name`.
    CONSTRAINT uq_sensors_name UNIQUE (name)
);

COMMENT ON TABLE  sensors IS
    'Physical IoT sensors deployed in the urban water-logging monitoring network.';
COMMENT ON COLUMN sensors.id IS
    'Surrogate primary key — auto-incremented by the database.';
COMMENT ON COLUMN sensors.name IS
    'Human-readable unique identifier (e.g. ''SENSOR_DHARAVI_01'').';
COMMENT ON COLUMN sensors.geometry IS
    'WGS84 geographic point (longitude, latitude). Stored natively in PostGIS.';
COMMENT ON COLUMN sensors.base_elevation IS
    'Sensor elevation above MSL in metres, sourced from the DEM pipeline.';
COMMENT ON COLUMN sensors.created_at IS
    'UTC timestamp of sensor registration. Set by the database server.';


-- -----------------------------------------------------------------------------
-- STEP 3: Create the `water_logs` table
-- -----------------------------------------------------------------------------
-- Append-only time-series table — one row per sensor reading.
--
-- Column notes:
--   id          — BIGSERIAL because high-frequency IoT streams can exceed
--                 ~2.1 billion rows (INT max) in months.
--   sensor_id   — FK to sensors.id with CASCADE so deleting a sensor
--                 automatically removes all its orphaned readings.
--   water_level — Depth in centimetres; negative = dry / sensor above water.
--   timestamp   — Server-side TIMESTAMPTZ matches created_at convention.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS water_logs (
    id          BIGSERIAL       PRIMARY KEY,
    sensor_id   INTEGER         NOT NULL,
    water_level FLOAT           NOT NULL,
    timestamp   TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Foreign key with cascade: parent sensor deletion removes all child logs.
    CONSTRAINT fk_water_logs_sensor_id
        FOREIGN KEY (sensor_id)
        REFERENCES sensors (id)
        ON DELETE CASCADE
        ON UPDATE CASCADE
);

COMMENT ON TABLE  water_logs IS
    'Append-only time-series table of water depth readings from IoT sensors.';
COMMENT ON COLUMN water_logs.id IS
    'BigInteger surrogate key — handles millions of IoT readings.';
COMMENT ON COLUMN water_logs.sensor_id IS
    'FK to the sensor that produced this reading.';
COMMENT ON COLUMN water_logs.water_level IS
    'Observed water depth in centimetres above the sensor reference datum.';
COMMENT ON COLUMN water_logs.timestamp IS
    'UTC timestamp when the reading was received by the database.';


-- -----------------------------------------------------------------------------
-- STEP 4: Create indexes
-- -----------------------------------------------------------------------------

-- 4a. Spatial (GiST) index on sensors.geometry
-- ----------------------------------------------
-- Enables:  bounding-box lookups, nearest-neighbour (KNN), ST_DWithin, etc.
-- Without this index, every spatial query performs a full table scan —
-- unacceptable with thousands of sensors or real-time map tile generation.
CREATE INDEX IF NOT EXISTS ix_sensors_geometry
    ON sensors USING GIST (geometry);

-- 4b. B-tree index on sensors.name
-- ---------------------------------
-- The UNIQUE constraint (Step 2) already creates this index implicitly.
-- Listed here for documentation completeness.
-- CREATE INDEX IF NOT EXISTS ix_sensors_name ON sensors (name);  -- (implicit)

-- 4c. Composite covering index on sensors(name, base_elevation)
-- --------------------------------------------------------------
-- Accelerates queries that filter by name AND select base_elevation without
-- touching the heap (index-only scan).
CREATE INDEX IF NOT EXISTS ix_sensors_name_elevation
    ON sensors (name, base_elevation);

-- 4d. B-tree index on water_logs.sensor_id
-- -----------------------------------------
-- The single most important index for time-series queries.
-- Query pattern: SELECT * FROM water_logs WHERE sensor_id = $1 ...
CREATE INDEX IF NOT EXISTS ix_water_logs_sensor_id
    ON water_logs (sensor_id);

-- 4e. B-tree index on water_logs.timestamp
-- -----------------------------------------
-- Enables efficient time-range scans across ALL sensors.
-- Query pattern: SELECT * FROM water_logs WHERE timestamp > NOW() - INTERVAL '1 hour'
CREATE INDEX IF NOT EXISTS ix_water_logs_timestamp
    ON water_logs (timestamp DESC);

-- 4f. Composite index on water_logs(sensor_id, timestamp DESC)
-- -------------------------------------------------------------
-- The critical "dashboard" index: latest N readings for a specific sensor.
-- Query pattern: SELECT * FROM water_logs
--                WHERE sensor_id = $1 ORDER BY timestamp DESC LIMIT 100;
-- PostgreSQL can satisfy this query entirely from the index (index-only scan)
-- when water_level is also projected if the index is extended to include it.
CREATE INDEX IF NOT EXISTS ix_water_logs_sensor_id_timestamp
    ON water_logs (sensor_id, timestamp DESC);


-- =============================================================================
-- Verification queries (run manually after applying migrations)
-- =============================================================================

-- Check PostGIS version
-- SELECT PostGIS_full_version();

-- List all tables
-- \dt

-- Inspect sensors columns
-- \d sensors

-- Inspect water_logs columns
-- \d water_logs

-- List all indexes
-- SELECT indexname, tablename, indexdef
-- FROM   pg_indexes
-- WHERE  schemaname = 'public'
-- ORDER  BY tablename, indexname;

-- Confirm spatial index exists (type = 'gist')
-- SELECT indexname, indexdef
-- FROM   pg_indexes
-- WHERE  indexdef ILIKE '%gist%';
