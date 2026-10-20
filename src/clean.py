"""M3 cleaning: builds the cleaned layer from the M2 tables.

Run from the repository root (after python src/load.py):
    python src/clean.py

Reads only the M2 tables (never raw files, never the API) and never changes them.
Rebuilds the cleaned tables inside ONE transaction, so it is idempotent and atomic.
Nothing is imputed: a value we do not trust becomes NULL and gets a flag; the raw
value is kept next to it. Thresholds: src/clean_config.py. Rule order: see that file.
"""

import csv
import hashlib
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

import clean_config as cfg
from db import ROOT, connect, init_clean_schema, init_schema

MAPPING_DIR = ROOT / "data" / "mappings"

# --- SQL (all values go through placeholders) ---------------------------------
M2_HASH_SQL = {   # fixed text: used to prove the M2 tables are identical before and after
    "aircraft": "SELECT * FROM aircraft ORDER BY aircraft_key",
    "aircraft_state": "SELECT * FROM aircraft_state ORDER BY aircraft_key, time_position",
    "location": "SELECT * FROM location ORDER BY location_key",
    "weather_hourly": "SELECT * FROM weather_hourly ORDER BY location_key, observed_at",
    "quarantine": "SELECT * FROM quarantine ORDER BY quarantine_id",
}
COUNT_SQL = {
    "aircraft_state": "SELECT COUNT(*) FROM aircraft_state",
    "aircraft_state_clean": "SELECT COUNT(*) FROM aircraft_state_clean",
    "weather_hourly": "SELECT COUNT(*) FROM weather_hourly",
    "weather_hourly_clean": "SELECT COUNT(*) FROM weather_hourly_clean",
    "location_airport": "SELECT COUNT(*) FROM location_airport",
}
SELECT_STATE = """
SELECT aircraft_key, time_position, on_ground, latitude, longitude, baro_altitude,
       geo_altitude, velocity, vertical_rate, callsign, squawk, retrieved_at
FROM aircraft_state
"""
SELECT_WEATHER = """
SELECT location_key, observed_at, wind_speed_10m, wind_gusts_10m, precipitation,
       showers, cape, retrieved_at
FROM weather_hourly
"""
SELECT_LOCATIONS = "SELECT location_key, location_index FROM location"

DELETE_STATE_CLEAN = "DELETE FROM aircraft_state_clean"
DELETE_WEATHER_CLEAN = "DELETE FROM weather_hourly_clean"
DELETE_MAPPING = "DELETE FROM location_airport"

INSERT_MAPPING = """
INSERT INTO location_airport (location_key, location_index, iata, airport_name,
                              decided_by, decided_at, note)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""
INSERT_STATE_CLEAN = """
INSERT INTO aircraft_state_clean (
    aircraft_key, time_position, on_ground,
    latitude_raw, latitude, latitude_flag,
    longitude_raw, longitude, longitude_flag,
    baro_altitude_raw, baro_altitude, baro_altitude_flag,
    geo_altitude_raw, geo_altitude, geo_altitude_flag,
    velocity_raw, velocity, velocity_flag,
    vertical_rate_raw, vertical_rate, vertical_rate_flag,
    callsign_raw, callsign, callsign_flag,
    squawk_raw, squawk, squawk_flag, retrieved_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
INSERT_WEATHER_CLEAN = """
INSERT INTO weather_hourly_clean (
    location_key, observed_at,
    wind_speed_10m_raw, wind_speed_10m, wind_speed_10m_flag,
    wind_gusts_10m_raw, wind_gusts_10m, wind_gusts_10m_flag,
    precipitation_raw, precipitation, precipitation_flag,
    showers_raw, showers, showers_flag,
    cape_raw, cape, cape_flag, retrieved_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

KEEP_FLAGS = {"ok", "unusual_high", "emergency_code", "trimmed"}   # flagged but value kept
STATE_COLUMNS = ["latitude", "longitude", "baro_altitude", "geo_altitude", "velocity",
                 "vertical_rate", "callsign", "squawk"]
WEATHER_COLUMNS = ["wind_speed_10m", "wind_gusts_10m", "precipitation", "showers", "cape"]


class CleanError(Exception):
    """Something is wrong with the inputs: abort, change nothing."""


def table_hashes(con):
    out = {}
    for table, sql in M2_HASH_SQL.items():
        h = hashlib.sha256()
        for row in con.execute(sql):
            h.update(repr(tuple(row)).encode("utf-8"))
        out[table] = h.hexdigest()
    return out


def cleaned(value, flag):
    """The cleaned value: the raw value if the flag keeps it, else NULL. Never imputed."""
    return value if flag in KEEP_FLAGS else None


# --- OpenSky rules -------------------------------------------------------------
def missing_flag(raw, on_ground, ground_explains):
    if raw is not None:
        return None
    return "missing_on_ground" if (ground_explains and on_ground == 1) else "missing_not_reported"


def altitude_flags(baro, geo, on_ground):
    """baro and geo altitude: missing -> invalid range -> the two contradict each other."""
    fb = missing_flag(baro, on_ground, True)
    fg = missing_flag(geo, on_ground, True)
    if fb is None and not (cfg.ALT_MIN_M <= baro <= cfg.ALT_MAX_M):
        fb = "invalid_range"
    if fg is None and not (cfg.ALT_MIN_M <= geo <= cfg.ALT_MAX_M):
        fg = "invalid_range"
    if fb is None and fg is None and abs(geo - baro) > cfg.ALT_GAP_MAX_M:
        fb = fg = "altitude_mismatch"   # which one is wrong is unknown: trust neither
    return fb or "ok", fg or "ok"


def range_flag(value, low, high, on_ground=None, ground_explains=False):
    flag = missing_flag(value, on_ground, ground_explains)
    if flag:
        return flag
    return "ok" if low <= value <= high else "invalid_range"


def callsign_clean(raw):
    """Standardise text only: trim whitespace; an empty string is a missing value."""
    if raw is None:
        return None, "missing_not_reported"
    trimmed = raw.strip()
    if trimmed == "":
        return None, "missing_empty"
    return trimmed, ("ok" if trimmed == raw else "trimmed")


def squawk_flag(raw):
    if raw is None:
        return "missing_not_reported"
    if len(raw) != 4 or any(ch not in "01234567" for ch in raw):
        return "invalid_format"
    return "emergency_code" if raw in cfg.EMERGENCY_SQUAWKS else "ok"


def clean_state_row(r):
    on_ground = r["on_ground"]
    lat_f = range_flag(r["latitude"], cfg.BBOX[0], cfg.BBOX[2])
    lon_f = range_flag(r["longitude"], cfg.BBOX[1], cfg.BBOX[3])
    baro_f, geo_f = altitude_flags(r["baro_altitude"], r["geo_altitude"], on_ground)
    vel_f = range_flag(r["velocity"], 0.0, cfg.VELOCITY_MAX_MS)
    vr_f = range_flag(r["vertical_rate"], -cfg.VERTICAL_RATE_MAX_MS, cfg.VERTICAL_RATE_MAX_MS,
                      on_ground, True)
    call, call_f = callsign_clean(r["callsign"])
    sq_f = squawk_flag(r["squawk"])
    flags = {"latitude": lat_f, "longitude": lon_f, "baro_altitude": baro_f,
             "geo_altitude": geo_f, "velocity": vel_f, "vertical_rate": vr_f,
             "callsign": call_f, "squawk": sq_f}
    row = (r["aircraft_key"], r["time_position"], on_ground,
           r["latitude"], cleaned(r["latitude"], lat_f), lat_f,
           r["longitude"], cleaned(r["longitude"], lon_f), lon_f,
           r["baro_altitude"], cleaned(r["baro_altitude"], baro_f), baro_f,
           r["geo_altitude"], cleaned(r["geo_altitude"], geo_f), geo_f,
           r["velocity"], cleaned(r["velocity"], vel_f), vel_f,
           r["vertical_rate"], cleaned(r["vertical_rate"], vr_f), vr_f,
           r["callsign"], call, call_f,
           r["squawk"], cleaned(r["squawk"], sq_f), sq_f,
           r["retrieved_at"])
    return row, flags


# --- Open-Meteo rules ----------------------------------------------------------
def hour(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%MZ")


def find_stuck(rows, column):
    """Keys (location_key, observed_at) inside a run of >= STUCK_RUN identical non-zero
    values in consecutive hours. Zero is excluded: calm weather legitimately repeats 0."""
    stuck = set()
    by_loc = {}
    for r in rows:
        by_loc.setdefault(r["location_key"], []).append(r)
    for loc_rows in by_loc.values():
        loc_rows.sort(key=lambda r: r["observed_at"])
        run = []
        for r in loc_rows:
            value = r[column]
            if (run and value is not None and value != 0
                    and value == run[-1][column]
                    and hour(r["observed_at"]) - hour(run[-1]["observed_at"]) == timedelta(hours=1)):
                run.append(r)
            else:
                if len(run) >= cfg.STUCK_RUN:
                    stuck.update((x["location_key"], x["observed_at"]) for x in run)
                run = [r] if (value is not None and value != 0) else []
        if len(run) >= cfg.STUCK_RUN:
            stuck.update((x["location_key"], x["observed_at"]) for x in run)
    return stuck


def gust_stats(rows):
    """Per location: median and MAD of plausible gust values (robust: not distorted by storms)."""
    values = {}
    for r in rows:
        g = r["wind_gusts_10m"]
        if g is not None and 0 <= g <= cfg.WIND_MAX_KMH:
            values.setdefault(r["location_key"], []).append(g)
    stats = {}
    for loc, vals in values.items():
        med = statistics.median(vals)
        stats[loc] = (med, statistics.median([abs(v - med) for v in vals]))
    return stats


def clean_weather_rows(rows):
    stuck = {c: find_stuck(rows, c) for c in ("wind_speed_10m", "wind_gusts_10m")}
    stats = gust_stats(rows)
    limits = {"wind_speed_10m": cfg.WIND_MAX_KMH, "wind_gusts_10m": cfg.WIND_MAX_KMH,
              "precipitation": cfg.PRECIP_MAX_MM_H, "showers": cfg.PRECIP_MAX_MM_H,
              "cape": cfg.CAPE_MAX_JKG}
    out = []
    for r in rows:
        key = (r["location_key"], r["observed_at"])
        flags = {}
        for col in WEATHER_COLUMNS:
            v = r[col]
            if v is None:
                flag = "missing_not_reported"
            elif not (0 <= v <= limits[col]):
                flag = "invalid_range"
            elif (col == "wind_gusts_10m" and r["wind_speed_10m"] is not None
                  and v < r["wind_speed_10m"]):
                flag = "invalid_gust_below_speed"
            elif (col == "showers" and r["precipitation"] is not None
                  and v > r["precipitation"]):
                flag = "invalid_showers_above_precip"
            elif col in stuck and key in stuck[col]:
                flag = "stuck_value"
            elif col == "wind_gusts_10m" and is_unusual_gust(r["location_key"], v, stats):
                flag = "unusual_high"
            elif col == "cape" and v > cfg.CAPE_UNUSUAL_JKG:
                flag = "unusual_high"
            else:
                flag = "ok"
            flags[col] = flag
        row = (r["location_key"], r["observed_at"])
        for col in WEATHER_COLUMNS:
            row += (r[col], cleaned(r[col], flags[col]), flags[col])
        out.append((row + (r["retrieved_at"],), flags, r["location_key"]))
    return out


def is_unusual_gust(loc, value, stats):
    med, mad = stats.get(loc, (None, 0))
    if not mad:
        return False
    return 0.6745 * (value - med) / mad > cfg.ROBUST_Z_THRESHOLD


# --- Mapping files -------------------------------------------------------------
def load_location_mapping(location_keys):
    """Read data/mappings/location_airport.csv. Returns (rows, notes about unmatched entries)."""
    path = MAPPING_DIR / cfg.MAPPING_FILES["location_airport"]
    if not path.exists():
        raise CleanError("missing mapping file: " + str(path))
    rows, notes, seen = [], [], set()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        needed = {"location_index", "iata", "airport_name", "decided_by", "decided_at", "note"}
        if not needed <= set(reader.fieldnames or []):
            raise CleanError("mapping file needs columns: " + ", ".join(sorted(needed)))
        for line in reader:
            try:
                index = int(line["location_index"])
            except ValueError:
                raise CleanError("bad location_index in mapping file: %r" % line["location_index"])
            if index not in location_keys:
                notes.append("mapping entry for location_index %d matched no location" % index)
                continue
            if not all(line[c].strip() for c in needed):
                raise CleanError("mapping entry for location_index %d has an empty field" % index)
            seen.add(index)
            rows.append((location_keys[index], index, line["iata"].strip(), line["airport_name"].strip(),
                         line["decided_by"].strip(), line["decided_at"].strip(), line["note"].strip()))
    for index in sorted(set(location_keys) - seen):
        notes.append("location_index %d has no mapping entry" % index)
    if any(r[4].upper().startswith("TODO") for r in rows):
        notes.append("decided_by is still a TODO placeholder in the mapping file")
    return rows, notes


# --- Run -----------------------------------------------------------------------
def count_flags(flag_dicts, columns):
    table = {c: {} for c in columns}
    for flags in flag_dicts:
        for c in columns:
            table[c][flags[c]] = table[c].get(flags[c], 0) + 1
    return table


def print_flag_table(title, table):
    print(title)
    for col, counts in table.items():
        parts = ", ".join("%s %d" % (f, n) for f, n in sorted(counts.items(), key=lambda x: -x[1]))
        print("  %-16s %s" % (col, parts))


def run(db_path=None):
    con = connect(db_path)
    try:
        init_schema(con)
        init_clean_schema(con)
        hashes_before = table_hashes(con)

        state_rows = con.execute(SELECT_STATE).fetchall()
        weather_rows = con.execute(SELECT_WEATHER).fetchall()
        location_keys = {r["location_index"]: r["location_key"] for r in con.execute(SELECT_LOCATIONS)}
        if not state_rows and not weather_rows:
            raise CleanError("the M2 tables are empty: run python src/load.py first")

        mapping_rows, mapping_notes = load_location_mapping(location_keys)
        iata_of = {r[0]: r[2] for r in mapping_rows}

        state_out = [clean_state_row(r) for r in state_rows]
        weather_out = clean_weather_rows(weather_rows)

        with con:   # ONE transaction: the cleaned layer is rebuilt completely or not at all
            con.execute(DELETE_STATE_CLEAN)
            con.execute(DELETE_WEATHER_CLEAN)
            con.execute(DELETE_MAPPING)
            con.executemany(INSERT_MAPPING, mapping_rows)
            con.executemany(INSERT_STATE_CLEAN, [row for row, _ in state_out])
            con.executemany(INSERT_WEATHER_CLEAN, [row for row, _, _ in weather_out])

        hashes_after = table_hashes(con)
        if hashes_before != hashes_after:
            raise CleanError("an M2 table changed during cleaning")   # must never happen
        counts = {t: con.execute(sql).fetchone()[0] for t, sql in COUNT_SQL.items()}
        fk_problems = con.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        con.close()

    print("aircraft_state_clean: %d rows (raw: %d)" % (counts["aircraft_state_clean"], counts["aircraft_state"]))
    print_flag_table("  flags per column:", count_flags([f for _, f in state_out], STATE_COLUMNS))
    print("weather_hourly_clean: %d rows (raw: %d)" % (counts["weather_hourly_clean"], counts["weather_hourly"]))
    print_flag_table("  flags per column:", count_flags([f for _, f, _ in weather_out], WEATHER_COLUMNS))
    print("  flags other than 'ok' per location:")
    per_loc = {}
    for _, flags, loc in weather_out:
        bucket = per_loc.setdefault(loc, {})
        for col, flag in flags.items():
            if flag != "ok":
                name = "%s:%s" % (col, flag)
                bucket[name] = bucket.get(name, 0) + 1
    for loc in sorted(per_loc, key=lambda k: iata_of.get(k, str(k))):
        parts = ", ".join("%s %d" % (k, v) for k, v in sorted(per_loc[loc].items())) or "none"
        print("  %-5s %s" % (iata_of.get(loc, "loc %s" % loc), parts))
    print("mappings: location_airport %d rows" % counts["location_airport"]
          + ("" if not mapping_notes else " . " + " . ".join(mapping_notes)))
    print("M2 tables unchanged: yes (hashes equal before and after)")
    ok = not fk_problems and counts["aircraft_state_clean"] == counts["aircraft_state"] \
        and counts["weather_hourly_clean"] == counts["weather_hourly"]
    print("foreign_key_check: %s" % ("OK" if not fk_problems else "%d VIOLATIONS" % len(fk_problems)))
    print("STATUS: COMPLETE" if ok else "STATUS: FAILED")
    return 0 if ok else 1


def main():
    try:
        return run()
    except Exception as exc:
        print("ERROR: %s: %s" % (type(exc).__name__, exc))
        print("the cleaned layer was left as it was before this run")
        print("STATUS: FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())