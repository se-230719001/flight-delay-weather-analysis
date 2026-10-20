-- M3 cleaned layer. Built from the M2 tables by src/clean.py; M2 tables are never changed.
-- Every table is STRICT. Each cleaned table has the grain, primary key and row count of the
-- M2 table it cleans. For every value cleaning can change: <col>_raw (exactly as loaded),
-- <col> (cleaned: NULL when not trusted, never imputed) and <col>_flag (why they differ).
-- Safe to run more than once.

-- MAPPING: weather location -> airport (loaded from data/mappings/location_airport.csv)
CREATE TABLE IF NOT EXISTS location_airport (
    location_key   INTEGER PRIMARY KEY REFERENCES location (location_key),
    location_index INTEGER NOT NULL UNIQUE,
    iata           TEXT NOT NULL UNIQUE,
    airport_name   TEXT NOT NULL,
    decided_by     TEXT NOT NULL,
    decided_at     TEXT NOT NULL,
    note           TEXT NOT NULL
) STRICT;

-- CLEANED: one row per aircraft state (grain and key of aircraft_state)
CREATE TABLE IF NOT EXISTS aircraft_state_clean (
    aircraft_key       INTEGER NOT NULL REFERENCES aircraft (aircraft_key),
    time_position      TEXT    NOT NULL,
    on_ground          INTEGER,
    latitude_raw       REAL,
    latitude           REAL,
    latitude_flag      TEXT NOT NULL CHECK (latitude_flag IN (
        'ok', 'missing_not_reported', 'invalid_range')),
    longitude_raw      REAL,
    longitude          REAL,
    longitude_flag     TEXT NOT NULL CHECK (longitude_flag IN (
        'ok', 'missing_not_reported', 'invalid_range')),
    baro_altitude_raw  REAL,
    baro_altitude      REAL,
    baro_altitude_flag TEXT NOT NULL CHECK (baro_altitude_flag IN (
        'ok', 'missing_on_ground', 'missing_not_reported', 'invalid_range',
        'altitude_mismatch')),
    geo_altitude_raw   REAL,
    geo_altitude       REAL,
    geo_altitude_flag  TEXT NOT NULL CHECK (geo_altitude_flag IN (
        'ok', 'missing_on_ground', 'missing_not_reported', 'invalid_range',
        'altitude_mismatch')),
    velocity_raw       REAL,
    velocity           REAL,
    velocity_flag      TEXT NOT NULL CHECK (velocity_flag IN (
        'ok', 'missing_not_reported', 'invalid_range')),
    vertical_rate_raw  REAL,
    vertical_rate      REAL,
    vertical_rate_flag TEXT NOT NULL CHECK (vertical_rate_flag IN (
        'ok', 'missing_on_ground', 'missing_not_reported', 'invalid_range')),
    callsign_raw       TEXT,
    callsign           TEXT,
    callsign_flag      TEXT NOT NULL CHECK (callsign_flag IN (
        'ok', 'trimmed', 'missing_empty', 'missing_not_reported')),
    squawk_raw         TEXT,
    squawk             TEXT,
    squawk_flag        TEXT NOT NULL CHECK (squawk_flag IN (
        'ok', 'missing_not_reported', 'invalid_format', 'emergency_code')),
    retrieved_at       TEXT NOT NULL,
    PRIMARY KEY (aircraft_key, time_position)
) STRICT;

-- CLEANED: one row per location and hour (grain and key of weather_hourly)
CREATE TABLE IF NOT EXISTS weather_hourly_clean (
    location_key        INTEGER NOT NULL REFERENCES location (location_key),
    observed_at         TEXT    NOT NULL,
    wind_speed_10m_raw  REAL,
    wind_speed_10m      REAL,
    wind_speed_10m_flag TEXT NOT NULL CHECK (wind_speed_10m_flag IN (
        'ok', 'missing_not_reported', 'invalid_range', 'stuck_value')),
    wind_gusts_10m_raw  REAL,
    wind_gusts_10m      REAL,
    wind_gusts_10m_flag TEXT NOT NULL CHECK (wind_gusts_10m_flag IN (
        'ok', 'missing_not_reported', 'invalid_range', 'invalid_gust_below_speed',
        'stuck_value', 'unusual_high')),
    precipitation_raw   REAL,
    precipitation       REAL,
    precipitation_flag  TEXT NOT NULL CHECK (precipitation_flag IN (
        'ok', 'missing_not_reported', 'invalid_range')),
    showers_raw         REAL,
    showers             REAL,
    showers_flag        TEXT NOT NULL CHECK (showers_flag IN (
        'ok', 'missing_not_reported', 'invalid_range', 'invalid_showers_above_precip')),
    cape_raw            REAL,
    cape                REAL,
    cape_flag           TEXT NOT NULL CHECK (cape_flag IN (
        'ok', 'missing_not_reported', 'invalid_range', 'unusual_high')),
    retrieved_at        TEXT NOT NULL,
    PRIMARY KEY (location_key, observed_at)
) STRICT;

-- Convenience views for M4: cleaned rows with entity information joined in.
CREATE VIEW IF NOT EXISTS v_aircraft_state_clean AS
SELECT a.icao24, a.origin_country, s.*
FROM aircraft_state_clean AS s
JOIN aircraft AS a ON a.aircraft_key = s.aircraft_key;

CREATE VIEW IF NOT EXISTS v_weather_hourly_clean AS
SELECT m.iata, m.airport_name, w.*
FROM weather_hourly_clean AS w
LEFT JOIN location_airport AS m ON m.location_key = w.location_key;