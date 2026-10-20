-- M2 schema: OpenSky aircraft states + Open-Meteo hourly weather.
-- Safe to run more than once. Every table is STRICT.
-- Timestamps: TEXT, ISO 8601 UTC, e.g. 2026-10-20T10:07:07Z. Dates: YYYY-MM-DD.
-- retrieved_at = timestamp of the landed raw file the row came from.

-- ENTITY: aircraft (source identifier: icao24)
CREATE TABLE IF NOT EXISTS aircraft (
    aircraft_key   INTEGER PRIMARY KEY,
    icao24         TEXT NOT NULL UNIQUE,
    origin_country TEXT,
    retrieved_at   TEXT NOT NULL
) STRICT;

-- EVENT: one reported state of one aircraft (OpenSky state vector)
CREATE TABLE IF NOT EXISTS aircraft_state (
    aircraft_key    INTEGER NOT NULL REFERENCES aircraft (aircraft_key),
    time_position   TEXT    NOT NULL,
    last_contact    TEXT,
    callsign        TEXT,
    longitude       REAL,
    latitude        REAL,
    baro_altitude   REAL,
    geo_altitude    REAL,
    on_ground       INTEGER CHECK (on_ground IN (0, 1)),
    velocity        REAL,
    true_track      REAL,
    vertical_rate   REAL,
    squawk          TEXT,
    spi             INTEGER CHECK (spi IN (0, 1)),
    position_source INTEGER,
    category        INTEGER,
    snapshot_time   TEXT,
    retrieved_at    TEXT NOT NULL,
    PRIMARY KEY (aircraft_key, time_position)
) STRICT;

-- ENTITY: weather location (source identifier: request order index 0..n;
-- the API omits location_id for the first location, so it is implicitly 0)
CREATE TABLE IF NOT EXISTS location (
    location_key   INTEGER PRIMARY KEY,
    location_index INTEGER NOT NULL UNIQUE,
    latitude       REAL,
    longitude      REAL,
    elevation      REAL,
    retrieved_at   TEXT NOT NULL
) STRICT;

-- EVENT: hourly weather values at one location
CREATE TABLE IF NOT EXISTS weather_hourly (
    location_key   INTEGER NOT NULL REFERENCES location (location_key),
    observed_at    TEXT    NOT NULL,
    wind_speed_10m REAL,
    wind_gusts_10m REAL,
    precipitation  REAL,
    showers        REAL,
    cape           REAL,
    retrieved_at   TEXT NOT NULL,
    PRIMARY KEY (location_key, observed_at)
) STRICT;

-- QUARANTINE: records that could not be loaded (missing key, unknown reference, ...)
CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_id INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    reason        TEXT NOT NULL,
    record_hash   TEXT NOT NULL,
    raw_record    TEXT NOT NULL,
    retrieved_at  TEXT NOT NULL,
    UNIQUE (source, record_hash, retrieved_at)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_state_time ON aircraft_state (time_position);