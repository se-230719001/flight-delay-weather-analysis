"""M3 validation: our expectations of the cleaned data, as code.

Run from the repository root:
    python src/validate.py             # check the real database
    python src/validate.py --selftest  # make every STOP check fail once, on a temporary copy

Each check prints PASS, WARN or STOP with a short detail. The exit code is non-zero if any
STOP check fails. WARNs never stop the pipeline but are printed on every run.
"""

import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import clean_config as cfg
from db import DB_PATH, connect

# --- SQL (fixed text; values only through placeholders) -------------------------
COUNT_PAIRS = [("aircraft_state_clean", "SELECT COUNT(*) FROM aircraft_state_clean",
                "aircraft_state", "SELECT COUNT(*) FROM aircraft_state"),
               ("weather_hourly_clean", "SELECT COUNT(*) FROM weather_hourly_clean",
                "weather_hourly", "SELECT COUNT(*) FROM weather_hourly")]

# Rows violating a rule that must hold for CLEANED values. Each query returns a count.
IMPOSSIBLE_SQL = {
    "latitude outside bbox": ("SELECT COUNT(*) FROM aircraft_state_clean WHERE latitude < ? OR latitude > ?",
                              (cfg.BBOX[0], cfg.BBOX[2])),
    "longitude outside bbox": ("SELECT COUNT(*) FROM aircraft_state_clean WHERE longitude < ? OR longitude > ?",
                               (cfg.BBOX[1], cfg.BBOX[3])),
    "altitude out of range": ("SELECT COUNT(*) FROM aircraft_state_clean WHERE baro_altitude < ? OR baro_altitude > ? "
                              "OR geo_altitude < ? OR geo_altitude > ?",
                              (cfg.ALT_MIN_M, cfg.ALT_MAX_M, cfg.ALT_MIN_M, cfg.ALT_MAX_M)),
    "negative velocity": ("SELECT COUNT(*) FROM aircraft_state_clean WHERE velocity < ? OR velocity > ?",
                          (0.0, cfg.VELOCITY_MAX_MS)),
    "altitudes contradict": ("SELECT COUNT(*) FROM aircraft_state_clean WHERE ABS(geo_altitude - baro_altitude) > ?",
                             (cfg.ALT_GAP_MAX_M,)),
    "negative weather value": ("SELECT COUNT(*) FROM weather_hourly_clean WHERE wind_speed_10m < ? OR wind_gusts_10m < ? "
                               "OR precipitation < ? OR showers < ? OR cape < ?", (0, 0, 0, 0, 0)),
    "gust below wind speed": ("SELECT COUNT(*) FROM weather_hourly_clean WHERE wind_gusts_10m < wind_speed_10m", ()),
    "showers above precipitation": ("SELECT COUNT(*) FROM weather_hourly_clean WHERE showers > precipitation", ()),
}

# No imputation: every non-NULL cleaned value equals its raw value.
IMPUTED_SQL = {
    "aircraft_state_clean": "SELECT COUNT(*) FROM aircraft_state_clean WHERE "
        "(latitude IS NOT NULL AND latitude IS NOT latitude_raw) OR "
        "(longitude IS NOT NULL AND longitude IS NOT longitude_raw) OR "
        "(baro_altitude IS NOT NULL AND baro_altitude IS NOT baro_altitude_raw) OR "
        "(geo_altitude IS NOT NULL AND geo_altitude IS NOT geo_altitude_raw) OR "
        "(velocity IS NOT NULL AND velocity IS NOT velocity_raw) OR "
        "(vertical_rate IS NOT NULL AND vertical_rate IS NOT vertical_rate_raw) OR "
        "(squawk IS NOT NULL AND squawk IS NOT squawk_raw) OR "
        "(callsign IS NOT NULL AND callsign IS NOT TRIM(callsign_raw))",
    "weather_hourly_clean": "SELECT COUNT(*) FROM weather_hourly_clean WHERE "
        "(wind_speed_10m IS NOT NULL AND wind_speed_10m IS NOT wind_speed_10m_raw) OR "
        "(wind_gusts_10m IS NOT NULL AND wind_gusts_10m IS NOT wind_gusts_10m_raw) OR "
        "(precipitation IS NOT NULL AND precipitation IS NOT precipitation_raw) OR "
        "(showers IS NOT NULL AND showers IS NOT showers_raw) OR "
        "(cape IS NOT NULL AND cape IS NOT cape_raw)",
}

UNUSABLE_BY_LOCATION = """
SELECT COALESCE(m.iata, CAST(w.location_key AS TEXT)) AS entity,
       COUNT(*) AS n,
       SUM(w.wind_speed_10m IS NULL) AS wind_speed, SUM(w.wind_gusts_10m IS NULL) AS gusts,
       SUM(w.precipitation IS NULL) AS precip, SUM(w.showers IS NULL) AS showers,
       SUM(w.cape IS NULL) AS cape
FROM weather_hourly_clean AS w
LEFT JOIN location_airport AS m ON m.location_key = w.location_key
GROUP BY w.location_key
"""
LATEST_BY_LOCATION = """
SELECT COALESCE(m.iata, CAST(l.location_key AS TEXT)) AS entity, MAX(w.observed_at) AS latest
FROM location AS l
LEFT JOIN weather_hourly_clean AS w ON w.location_key = l.location_key
LEFT JOIN location_airport AS m ON m.location_key = l.location_key
GROUP BY l.location_key
"""
AIRBORNE_ALT = ("SELECT COUNT(*), SUM(baro_altitude IS NOT NULL) FROM aircraft_state_clean WHERE on_ground = 0")
NEWEST_RETRIEVAL = "SELECT MAX(retrieved_at) FROM aircraft_state"
COUNT_ALTITUDE_MISMATCH = "SELECT COUNT(*) FROM aircraft_state_clean WHERE baro_altitude_flag = 'altitude_mismatch'"
COUNT_STUCK = "SELECT COUNT(*) FROM weather_hourly_clean WHERE wind_speed_10m_flag = 'stuck_value' OR wind_gusts_10m_flag = 'stuck_value'"
COUNT_EMERGENCY = "SELECT COUNT(*) FROM aircraft_state_clean WHERE squawk_flag = 'emergency_code'"
COUNT_QUARANTINE = "SELECT COUNT(*) FROM quarantine"


def scalar(con, sql, params=()):
    return con.execute(sql, params).fetchone()[0]


# --- Checks: each returns (status, name, detail). STOP checks use PASS / STOP, WARN checks PASS / WARN
def check_row_counts(con):
    bad = []
    for clean_t, clean_sql, raw_t, raw_sql in COUNT_PAIRS:
        a, b = scalar(con, clean_sql), scalar(con, raw_sql)
        if a != b:
            bad.append("%s %d != %s %d" % (clean_t, a, raw_t, b))
    name = "cleaned tables have the row count of their raw tables"
    if bad:
        return "STOP", name, "; ".join(bad)
    return "PASS", name, ", ".join("%s = %d" % (c, scalar(con, cs)) for c, cs, _, _ in COUNT_PAIRS)


def check_impossible(con):
    found = {k: scalar(con, sql, p) for k, (sql, p) in IMPOSSIBLE_SQL.items()}
    bad = {k: v for k, v in found.items() if v}
    name = "no impossible value survives in a cleaned column"
    if bad:
        return "STOP", name, ", ".join("%s: %d" % kv for kv in bad.items())
    return "PASS", name, "%d rules, 0 violations" % len(found)


def check_no_imputation(con):
    bad = {t: scalar(con, sql) for t, sql in IMPUTED_SQL.items()}
    bad = {t: n for t, n in bad.items() if n}
    name = "no imputation: every cleaned value equals its raw value"
    if bad:
        return "STOP", name, ", ".join("%s: %d rows" % kv for kv in bad.items())
    return "PASS", name, "0 differing values"


def check_foreign_keys(con):
    problems = con.execute("PRAGMA foreign_key_check").fetchall()
    name = "foreign_key_check"
    if problems:
        return "STOP", name, "%d violations" % len(problems)
    return "PASS", name, "OK"


def check_unusable_share(con):
    worst, bad = 0.0, []
    for r in con.execute(UNUSABLE_BY_LOCATION).fetchall():
        for var, missing in zip(("wind_speed", "gusts", "precip", "showers", "cape"), r[2:]):
            share = missing / r[1]
            worst = max(worst, share)
            if share > cfg.UNUSABLE_SHARE_MAX:
                bad.append("%s %s %.0f%%" % (r[0], var, 100 * share))
    name = "weather: NULL share per location at most %.0f%%" % (100 * cfg.UNUSABLE_SHARE_MAX)
    if bad:
        return "WARN", name, "; ".join(bad)
    return "PASS", name, "worst share %.1f%%" % (100 * worst)


def check_airborne_altitude(con):
    total, usable = con.execute(AIRBORNE_ALT).fetchone()
    share = (usable or 0) / total if total else 0.0
    name = "airborne states keep a usable baro altitude (at least %.0f%%)" % (100 * cfg.AIRBORNE_ALT_USABLE_MIN)
    status = "PASS" if share >= cfg.AIRBORNE_ALT_USABLE_MIN else "WARN"
    return status, name, "%.1f%% of %d airborne states" % (100 * share, total)


def check_coverage(con):
    latest = {r[0]: r[1] for r in con.execute(LATEST_BY_LOCATION)}
    newest = max((v for v in latest.values() if v), default=None)
    behind = [k for k, v in latest.items() if v != newest]
    name = "every location has weather up to the most recent hour in the database"
    if newest is None:
        return "WARN", name, "no weather data"
    if behind:
        return "WARN", name, "behind: " + ", ".join(behind)
    return "PASS", name, "%d of %d locations, newest hour %s" % (len(latest), len(latest), newest)


def check_freshness(con):
    newest = scalar(con, NEWEST_RETRIEVAL)
    name = "newest raw retrieval is at most %d h old" % cfg.FRESHNESS_HOURS
    if not newest:
        return "WARN", name, "no data"
    age = datetime.now(timezone.utc) - datetime.strptime(newest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    status = "PASS" if age <= timedelta(hours=cfg.FRESHNESS_HOURS) else "WARN"
    return status, name, "newest %s (%.1f h ago)" % (newest, age.total_seconds() / 3600)


def count_check(sql, label, extra=""):
    def check(con):
        n = scalar(con, sql)
        return ("PASS" if n == 0 else "WARN"), "no %s" % label, "%d%s" % (n, extra)
    return check


STOP_CHECKS = [check_row_counts, check_impossible, check_no_imputation, check_foreign_keys]
WARN_CHECKS = [check_unusable_share, check_airborne_altitude, check_coverage, check_freshness,
               count_check(COUNT_STUCK, "stuck_value flags (wind repeated %d h)" % cfg.STUCK_RUN),
               count_check(COUNT_ALTITUDE_MISMATCH, "altitude_mismatch flags (baro vs geo contradict)"),
               count_check(COUNT_EMERGENCY, "emergency squawk codes (7500/7600/7700)"),
               count_check(COUNT_QUARANTINE, "quarantined records")]


def run_all(con, verbose=True):
    results = []
    for check in STOP_CHECKS + WARN_CHECKS:
        status, name, detail = check(con)
        results.append((status, name, detail))
        if verbose:
            print("%-5s %-72s %s" % (status, name, detail))
    return results


def selftest(db_path):
    """Make each STOP check fail once, on a temporary copy of the database."""
    sabotage = [
        ("row counts", check_row_counts,
         "DELETE FROM weather_hourly_clean WHERE rowid = (SELECT MIN(rowid) FROM weather_hourly_clean)"),
        ("impossible value", check_impossible,
         "UPDATE aircraft_state_clean SET baro_altitude = -999 WHERE rowid = (SELECT MIN(rowid) FROM aircraft_state_clean)"),
        ("no imputation", check_no_imputation,
         "UPDATE weather_hourly_clean SET wind_speed_10m = wind_speed_10m_raw + 1 "
         "WHERE rowid = (SELECT MIN(rowid) FROM weather_hourly_clean WHERE wind_speed_10m_raw IS NOT NULL)"),
        ("foreign_key_check", check_foreign_keys,
         "UPDATE aircraft_state_clean SET aircraft_key = 999999 WHERE rowid = (SELECT MIN(rowid) FROM aircraft_state_clean)"),
    ]
    all_ok = True
    for label, check, sql in sabotage:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "selftest.db"
            shutil.copyfile(db_path, copy)
            con = connect(copy)
            con.execute("PRAGMA foreign_keys = OFF")   # only so the temporary copy can hold an orphan
            with con:
                con.execute(sql)
            con.execute("PRAGMA foreign_keys = ON")
            status, _, detail = check(con)
            con.close()
        ok = status == "STOP"
        all_ok = all_ok and ok
        print("selftest %-18s -> %-5s %s" % (label, status, detail))
    print("SELFTEST OK: every STOP check fails when it should" if all_ok else "SELFTEST FAILED")
    return 0 if all_ok else 1


def main(argv):
    path = DB_PATH
    if not Path(path).exists():
        print("ERROR: %s not found: run python src/load.py and python src/clean.py first" % path)
        return 1
    if "--selftest" in argv:
        return selftest(path)
    con = connect(path)
    try:
        results = run_all(con)
    finally:
        con.close()
    stops = [r for r in results if r[0] == "STOP"]
    warns = [r for r in results if r[0] == "WARN"]
    print("%d checks: %d PASS, %d WARN, %d STOP" % (len(results), len(results) - len(stops) - len(warns),
                                                  len(warns), len(stops)))
    print("STATUS: FAILED" if stops else "STATUS: COMPLETE")
    return 1 if stops else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))