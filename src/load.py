"""M2 loader: reads landed raw files (data/raw/...) into the SQLite schema.

Run from the repository root:
    python src/load.py

Properties: idempotent (upserts, latest retrieved_at wins), atomic (one transaction),
parameterised (no values formatted into SQL), honest (run summary at the end).
"""

import hashlib
import json
import math
import sys
from pathlib import Path

from db import (DB_PATH, ROOT, connect, epoch_to_iso, init_schema,
                meteo_time_to_iso, retrieved_at_from_filename)

RAW_DIR = ROOT / "data" / "raw"
METEO_VARS = ["wind_speed_10m", "wind_gusts_10m", "precipitation", "showers", "cape"]

# --- SQL (all values go through placeholders) ---------------------------------
UPSERT_AIRCRAFT = """
INSERT INTO aircraft (icao24, origin_country, retrieved_at) VALUES (?, ?, ?)
ON CONFLICT (icao24) DO UPDATE SET
    origin_country = excluded.origin_country,
    retrieved_at   = excluded.retrieved_at
WHERE excluded.retrieved_at >= aircraft.retrieved_at
"""
UPSERT_STATE = """
INSERT INTO aircraft_state (aircraft_key, time_position, last_contact, callsign, longitude,
    latitude, baro_altitude, geo_altitude, on_ground, velocity, true_track, vertical_rate,
    squawk, spi, position_source, category, snapshot_time, retrieved_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (aircraft_key, time_position) DO UPDATE SET
    last_contact = excluded.last_contact, callsign = excluded.callsign,
    longitude = excluded.longitude, latitude = excluded.latitude,
    baro_altitude = excluded.baro_altitude, geo_altitude = excluded.geo_altitude,
    on_ground = excluded.on_ground, velocity = excluded.velocity,
    true_track = excluded.true_track, vertical_rate = excluded.vertical_rate,
    squawk = excluded.squawk, spi = excluded.spi,
    position_source = excluded.position_source, category = excluded.category,
    snapshot_time = excluded.snapshot_time, retrieved_at = excluded.retrieved_at
WHERE excluded.retrieved_at >= aircraft_state.retrieved_at
"""
UPSERT_LOCATION = """
INSERT INTO location (location_index, latitude, longitude, elevation, retrieved_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (location_index) DO UPDATE SET
    latitude = excluded.latitude, longitude = excluded.longitude,
    elevation = excluded.elevation, retrieved_at = excluded.retrieved_at
WHERE excluded.retrieved_at >= location.retrieved_at
"""
UPSERT_WEATHER = """
INSERT INTO weather_hourly (location_key, observed_at, wind_speed_10m, wind_gusts_10m,
    precipitation, showers, cape, retrieved_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (location_key, observed_at) DO UPDATE SET
    wind_speed_10m = excluded.wind_speed_10m, wind_gusts_10m = excluded.wind_gusts_10m,
    precipitation = excluded.precipitation, showers = excluded.showers,
    cape = excluded.cape, retrieved_at = excluded.retrieved_at
WHERE excluded.retrieved_at >= weather_hourly.retrieved_at
"""
INSERT_QUARANTINE = """
INSERT INTO quarantine (source, reason, record_hash, raw_record, retrieved_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (source, record_hash, retrieved_at) DO NOTHING
"""
COUNT_SQL = {
    "aircraft": "SELECT COUNT(*) FROM aircraft",
    "aircraft_state": "SELECT COUNT(*) FROM aircraft_state",
    "location": "SELECT COUNT(*) FROM location",
    "weather_hourly": "SELECT COUNT(*) FROM weather_hourly",
    "quarantine": "SELECT COUNT(*) FROM quarantine",
}


class LoadError(Exception):
    """Raw data is structurally broken: abort the whole run, write nothing."""


class Stats:
    def __init__(self):
        self.cleaned = 0          # non-null values that did not fit their column -> NULL
        self.quarantined = {}     # (source, hash, retrieved_at) -> row, de-duplicated


# --- Validation helpers ---------------------------------------------------------
def to_real(value, stats):
    """Number -> float. null -> NULL (not recorded). Anything else -> NULL, counted."""
    if value is None:
        return None
    if isinstance(value, bool):
        stats.cleaned += 1
        return None
    if isinstance(value, (int, float)):
        if math.isfinite(value):
            return float(value)
        stats.cleaned += 1
        return None
    if isinstance(value, str):
        try:
            number = float(value)
            if math.isfinite(number):
                return number
        except ValueError:
            pass
    stats.cleaned += 1
    return None


def to_int(value, stats):
    number = to_real(value, stats)
    return None if number is None else int(number)


def to_flag(value, stats):
    """Boolean -> 0/1. null -> NULL. Anything else -> NULL, counted."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    stats.cleaned += 1
    return None


def to_text(value, stats):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    stats.cleaned += 1
    return None


def to_time(value, stats):
    """Epoch seconds -> ISO 8601 UTC. null -> NULL. Invalid -> NULL, counted."""
    if value is None:
        return None
    iso = epoch_to_iso(value) if not isinstance(value, (bool, str)) else None
    if iso is None:
        stats.cleaned += 1
    return iso


def quarantine(stats, source, reason, record, retrieved_at):
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    stats.quarantined[(source, digest, retrieved_at)] = (source, reason, digest, raw, retrieved_at)


def newer(candidate, current):
    """Latest retrieval wins; the file name only breaks exact ties."""
    return current is None or candidate[0] >= current[0]


# --- Reading raw files ----------------------------------------------------------
def list_raw(raw_dir, source):
    files = []
    for path in (Path(raw_dir) / source).glob("*.json"):
        files.append((retrieved_at_from_filename(path), path.name, path))
    return sorted(files)


def read_json(path):
    try:
        return json.loads(path.read_bytes())
    except ValueError as exc:
        raise LoadError(f"{path.name}: not valid JSON ({exc})")


def parse_opensky(files, stats):
    aircraft, states = {}, {}   # icao24 -> (sort key, row); (icao24, time_position) -> ...
    for retrieved_at, name, path in files:
        data = read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("states"), list):
            raise LoadError(f"{name}: expected an object with a 'states' list")
        snapshot = to_time(data.get("time"), stats)
        order = (retrieved_at, name)
        for s in data["states"]:
            if not isinstance(s, list) or len(s) not in (17, 18):
                raise LoadError(f"{name}: state vector has an unexpected shape")
            icao = s[0].strip().lower() if isinstance(s[0], str) and s[0].strip() else None
            time_position = to_time(s[3], stats)
            if icao is None or time_position is None:
                reason = "missing icao24" if icao is None else "missing or invalid time_position"
                quarantine(stats, "opensky", reason, s, retrieved_at)
                continue
            country = to_text(s[2], stats)
            cur = aircraft.get(icao)
            if newer((order,), cur and (cur[0],)):
                aircraft[icao] = (order, (icao, country, retrieved_at))
            row = (to_time(s[4], stats), to_text(s[1], stats), to_real(s[5], stats),
                   to_real(s[6], stats), to_real(s[7], stats), to_real(s[13], stats),
                   to_flag(s[8], stats), to_real(s[9], stats), to_real(s[10], stats),
                   to_real(s[11], stats), to_text(s[14], stats), to_flag(s[15], stats),
                   to_int(s[16], stats), to_int(s[17], stats) if len(s) == 18 else None,
                   snapshot, retrieved_at)
            key = (icao, time_position)
            cur = states.get(key)
            if newer((order,), cur and (cur[0],)):
                states[key] = (order, row)
    return aircraft, states


def parse_openmeteo(files, stats):
    locations, weather = {}, {}   # index -> ...; (index, observed_at) -> ...
    for retrieved_at, name, path in files:
        data = read_json(path)
        items = data if isinstance(data, list) else [data]
        order = (retrieved_at, name)
        for index, loc in enumerate(items):
            if not isinstance(loc, dict) or not isinstance(loc.get("hourly"), dict):
                raise LoadError(f"{name}: location {index} has no 'hourly' object")
            hourly = loc["hourly"]
            times = hourly.get("time")
            if not isinstance(times, list):
                raise LoadError(f"{name}: location {index} has no 'time' list")
            for var in METEO_VARS:
                if var in hourly and (not isinstance(hourly[var], list)
                                      or len(hourly[var]) != len(times)):
                    raise LoadError(f"{name}: location {index}: '{var}' does not match 'time'")
            cur = locations.get(index)
            if newer((order,), cur and (cur[0],)):
                locations[index] = (order, (index, to_real(loc.get("latitude"), stats),
                                            to_real(loc.get("longitude"), stats),
                                            to_real(loc.get("elevation"), stats), retrieved_at))
            for i, t in enumerate(times):
                observed_at = meteo_time_to_iso(t)
                if observed_at is None:
                    quarantine(stats, "openmeteo", "invalid hourly time",
                               {"location_index": index, "time": t}, retrieved_at)
                    continue
                values = tuple(to_real(hourly[v][i], stats) if v in hourly else None
                               for v in METEO_VARS)
                key = (index, observed_at)
                cur = weather.get(key)
                if newer((order,), cur and (cur[0],)):
                    weather[key] = (order, values + (retrieved_at,))
    return locations, weather


# --- Writing --------------------------------------------------------------------
def counts(con):
    return {t: con.execute(sql).fetchone()[0] for t, sql in COUNT_SQL.items()}


def write_all(con, aircraft, states, locations, weather, stats):
    for _, row in aircraft.values():
        con.execute(UPSERT_AIRCRAFT, row)
    for _, row in locations.values():
        con.execute(UPSERT_LOCATION, row)
    aircraft_key = {r[0]: r[1] for r in con.execute("SELECT icao24, aircraft_key FROM aircraft")}
    location_key = {r[0]: r[1] for r in con.execute("SELECT location_index, location_key FROM location")}

    state_rows = []
    for (icao, time_position), (_, row) in states.items():
        if icao not in aircraft_key:   # unknown reference -> quarantine, never drop or crash
            quarantine(stats, "opensky", "unknown aircraft", [icao, time_position], row[-1])
            continue
        state_rows.append((aircraft_key[icao], time_position) + row)
    con.executemany(UPSERT_STATE, state_rows)

    weather_rows = []
    for (index, observed_at), (_, values) in weather.items():
        if index not in location_key:
            quarantine(stats, "openmeteo", "unknown location", [index, observed_at], values[-1])
            continue
        weather_rows.append((location_key[index], observed_at) + values)
    con.executemany(UPSERT_WEATHER, weather_rows)

    con.executemany(INSERT_QUARANTINE, list(stats.quarantined.values()))


def run(db_path=None, raw_dir=None):
    raw_dir = Path(raw_dir) if raw_dir else RAW_DIR
    stats = Stats()
    sky_files, meteo_files = list_raw(raw_dir, "opensky"), list_raw(raw_dir, "openmeteo")
    all_files = sky_files + meteo_files
    if not all_files:
        raise LoadError(f"no raw files found in {raw_dir}")

    # Parse and validate everything first; any structural problem aborts before writing.
    aircraft, states = parse_opensky(sky_files, stats)
    locations, weather = parse_openmeteo(meteo_files, stats)

    con = connect(db_path)
    try:
        init_schema(con)
        before = counts(con)
        with con:   # ONE transaction: commit on success, roll back on any exception
            write_all(con, aircraft, states, locations, weather, stats)
        after = counts(con)
        fk_problems = con.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        con.close()

    stamps = sorted(f[0] for f in all_files)
    print(f"files read: {len(all_files)} raw files ({stamps[0][:10]} .. {stamps[-1][:10]})")
    for table, label in [("aircraft", "aircraft"), ("aircraft_state", "aircraft states"),
                         ("location", "locations"), ("weather_hourly", "weather hours")]:
        print(f"{label}: {after[table]:,} in database (+{after[table] - before[table]:,} this run)")
    print(f"values cleaned: {stats.cleaned} (invalid non-null values -> NULL)")
    print(f"quarantined: {len(stats.quarantined)} records "
          f"({after['quarantine'] - before['quarantine']} new this run)")
    print(f"foreign_key_check: {'OK' if not fk_problems else f'{len(fk_problems)} VIOLATIONS'}")
    print("STATUS: COMPLETE" if not fk_problems else "STATUS: FAILED")
    return 0 if not fk_problems else 1


def main():
    try:
        return run()
    except Exception as exc:   # nothing from this run was written
        print(f"ERROR: {type(exc).__name__}: {exc}")
        print("nothing was written to the database")
        print("STATUS: FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())