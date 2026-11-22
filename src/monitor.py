"""M5 monitor: the checks that run OUTSIDE the pipeline.

    python src/monitor.py [--now 2026-11-20T09:30Z]      (default: now)

Reads the run log (data/pipeline_log.db) and the main database READ-ONLY, prints one line per
check (PASS or ALARM, plus a short detail) and exits 1 if any check raised an ALARM.
A pipeline that did not run cannot report that it did not run: this script is scheduled
separately (ops/install_task.ps1) so it still runs when the pipeline's own schedule breaks.

Every threshold below can be overridden with an environment variable of the same name. That
is only for the "make each check ALARM once" drills in docs/pipeline.md; leave them unset.
"""

import argparse
import os
import sqlite3
import sys
from datetime import timedelta

from db import DB_PATH, LOG_DB_PATH, format_run_time, parse_run_time, resolve_run_time


def _env(name, default):
    return float(os.environ.get(name, default))


# --- Thresholds (each with the reason for its value) -----------------------------------
# The schedule is hourly, but the machine is only switched on about once a day, so a normal
# night is a ~16-20 h silence. 36 h means "a whole day was skipped", not "the laptop slept".
LAST_SUCCESS_MAX_HOURS = _env("LAST_SUCCESS_MAX_HOURS", 36)
# Same reasoning: the longest normal pause (off overnight, on late the next day) is about
# 34 h; anything beyond is a skipped day. OpenSky cannot be back-filled, so a gap = lost data.
MAX_GAP_HOURS = _env("MAX_GAP_HOURS", 36)
GAP_WINDOW_DAYS = _env("GAP_WINDOW_DAYS", 7)          # the evidence period is 7 days
# A full run has 4 steps (ingest, load, clean, validate). A --skip-ingest rebuild has 3 and
# is deliberately NOT counted as a success: it fetched nothing.
STEPS_PER_FULL_RUN = int(_env("STEPS_PER_FULL_RUN", 4))
# Open-Meteo is hourly and heals itself (past_days), so every hour of the last 72 h must have
# a row for every location. Hours newer than 2 h before --now may still be forecast.
COMPLETENESS_HOURS = int(_env("COMPLETENESS_HOURS", 72))
COMPLETENESS_LAG_HOURS = int(_env("COMPLETENESS_LAG_HOURS", 2))
# After a pipeline run, every feed's newest value should be recent. 12 h tolerates a monitor
# that runs a few minutes before the pipeline has finished after a late switch-on, yet
# catches a feed that has stopped while the machine is on.
FRESHNESS_MAX_HOURS = _env("FRESHNESS_MAX_HOURS", 12)

ISO_MINUTE = "%Y-%m-%dT%H:%MZ"

# --- SQL (fixed text; every value goes through a placeholder) ----------------------------
LAST_SUCCESS_SQL = """
SELECT MAX(run_time) AS last_success,
       ROUND((julianday(:now) - julianday(MAX(run_time))) * 24, 1) AS hours_ago
FROM (SELECT run_id, run_time FROM pipeline_run
      WHERE julianday(run_time) <= julianday(:now)
      GROUP BY run_id HAVING COUNT(*) = :steps AND MAX(exit_code) = 0)
"""
GAPS_SQL = """
WITH runs AS (SELECT DISTINCT run_time FROM pipeline_run
              WHERE julianday(run_time) >= julianday(:since)
                AND julianday(run_time) <= julianday(:now))
SELECT * FROM (
    SELECT LAG(run_time) OVER (ORDER BY run_time) AS previous_run, run_time,
           ROUND((julianday(run_time)
                  - julianday(LAG(run_time) OVER (ORDER BY run_time))) * 24, 1) AS hours_since
    FROM runs)
WHERE hours_since > :max_gap_hours
ORDER BY hours_since DESC
"""
EXPECTED_LOCATIONS_SQL = "SELECT COUNT(*) FROM location"
COMPLETENESS_SQL = """
WITH RECURSIVE periods(p) AS (
    SELECT :first_period
    UNION ALL
    SELECT strftime('%Y-%m-%dT%H:%MZ', p, '+1 hour') FROM periods WHERE p < :last_period)
SELECT p AS period, COUNT(w.location_key) AS entities
FROM periods LEFT JOIN weather_hourly AS w ON w.observed_at = p
GROUP BY p
HAVING COUNT(w.location_key) < :expected_entities
ORDER BY p
"""
WEATHER_FRESHNESS_SQL = """
SELECT l.location_key, MAX(w.observed_at) AS latest
FROM location AS l
LEFT JOIN weather_hourly_clean AS w
       ON w.location_key = l.location_key AND w.is_forecast = 0 AND w.wind_speed_10m IS NOT NULL
GROUP BY l.location_key
HAVING MAX(w.observed_at) IS NULL
    OR julianday(MAX(w.observed_at)) < julianday(:now) - :max_hours / 24.0
"""
AIRCRAFT_FRESHNESS_SQL = """
SELECT MAX(retrieved_at) AS latest FROM aircraft_state
"""
SNAPSHOT_HOURS_SQL = """
SELECT COUNT(DISTINCT substr(retrieved_at, 1, 13)) FROM aircraft_state
WHERE julianday(retrieved_at) > julianday(:now) - 1 AND julianday(retrieved_at) <= julianday(:now)
"""


def open_readonly(path):
    """Read-only connection, or None if the file does not exist (never creates a database)."""
    if not path.exists():
        return None
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def minute(dt):
    return dt.strftime(ISO_MINUTE)


# --- The four checks. Each returns (ok, detail). ----------------------------------------
def check_last_success(log_con, db_con, now):
    if log_con is None:
        return False, "no run log at %s: the pipeline has never run" % LOG_DB_PATH
    row = log_con.execute(LAST_SUCCESS_SQL, {"now": minute(now), "steps": STEPS_PER_FULL_RUN}).fetchone()
    if row[0] is None:
        return False, "no run with all %d steps at exit code 0" % STEPS_PER_FULL_RUN
    ok = row[1] <= LAST_SUCCESS_MAX_HOURS
    return ok, "last full success %s (%.1f h before now, limit %g h)" % (row[0], row[1], LAST_SUCCESS_MAX_HOURS)


def check_gaps(log_con, db_con, now):
    if log_con is None:
        return False, "no run log"
    since = now - timedelta(days=GAP_WINDOW_DAYS)
    rows = log_con.execute(GAPS_SQL, {"since": minute(since), "now": minute(now),
                                      "max_gap_hours": MAX_GAP_HOURS}).fetchall()
    n_runs = log_con.execute(
        "SELECT COUNT(DISTINCT run_time) FROM pipeline_run "
        "WHERE julianday(run_time) >= julianday(?) AND julianday(run_time) <= julianday(?)",
        (minute(since), minute(now))).fetchone()[0]
    if not rows:
        return True, "%d runs in the last %g days, none more than %g h apart" % (n_runs, GAP_WINDOW_DAYS, MAX_GAP_HOURS)
    worst = "; ".join("%s -> %s (%.1f h)" % (r[0], r[1], r[2]) for r in rows[:3])
    return False, "%d gap(s) over %g h in the last %g days: %s" % (len(rows), MAX_GAP_HOURS, GAP_WINDOW_DAYS, worst)


def check_completeness(log_con, db_con, now):
    if db_con is None:
        return False, "no database at %s" % DB_PATH
    expected = db_con.execute(EXPECTED_LOCATIONS_SQL).fetchone()[0]
    floor_hour = now.replace(minute=0, second=0, microsecond=0)
    last = floor_hour - timedelta(hours=COMPLETENESS_LAG_HOURS)
    first = last - timedelta(hours=COMPLETENESS_HOURS - 1)
    rows = db_con.execute(COMPLETENESS_SQL, {"first_period": minute(first), "last_period": minute(last),
                                             "expected_entities": expected}).fetchall()
    span = "%d h, %s .. %s, %d locations" % (COMPLETENESS_HOURS, minute(first), minute(last), expected)
    if expected == 0:
        return False, "the location table is empty"
    if not rows:
        return True, "every hour has every location (%s)" % span
    sample = ", ".join("%s (%d of %d)" % (r[0], r[1], expected) for r in rows[:3])
    return False, "%d incomplete hour(s) in %s; first: %s" % (len(rows), span, sample)


def check_freshness(log_con, db_con, now):
    if db_con is None:
        return False, "no database at %s" % DB_PATH
    stale = db_con.execute(WEATHER_FRESHNESS_SQL, {"now": minute(now), "max_hours": FRESHNESS_MAX_HOURS}).fetchall()
    problems = ["weather location %s newest observed %s" % (r[0], r[1] or "never") for r in stale]
    newest = db_con.execute(AIRCRAFT_FRESHNESS_SQL).fetchone()[0]
    if newest is None:
        problems.append("no aircraft data at all")
    else:
        age_h = (now - parse_run_time(newest[:16] + "Z")).total_seconds() / 3600
        if age_h > FRESHNESS_MAX_HOURS:
            problems.append("opensky newest retrieval %s (%.1f h before now)" % (newest, age_h))
    if problems:
        return False, "limit %g h: %s" % (FRESHNESS_MAX_HOURS, "; ".join(problems))
    return True, "weather (observed hours) and opensky are all newer than %g h before now" % FRESHNESS_MAX_HOURS


def info_snapshot_hours(db_con, now):
    """Not an alarm: OpenSky cannot be back-filled, so this shows how much of today is covered."""
    if db_con is None:
        return "no database"
    n = db_con.execute(SNAPSHOT_HOURS_SQL, {"now": minute(now)}).fetchone()[0]
    return "OpenSky snapshots cover %d of the last 24 hours (a snapshot source: missed hours are lost)" % n


CHECKS = [
    ("last success", check_last_success),
    ("gaps between runs", check_gaps),
    ("completeness vs calendar", check_completeness),
    ("freshness from --now", check_freshness),
]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check the pipeline from the outside.")
    parser.add_argument("--now", dest="run_time", default=None,
                        type=lambda s: parse_run_time(s), help="the clock, e.g. 2026-11-20T09:30Z (default: now)")
    args = parser.parse_args(argv)
    now = resolve_run_time(args.run_time)

    log_con = open_readonly(LOG_DB_PATH)
    db_con = open_readonly(DB_PATH)
    alarms = 0
    print("monitor.py at %s" % format_run_time(now))
    try:
        for name, check in CHECKS:
            try:
                ok, detail = check(log_con, db_con, now)
            except sqlite3.Error as exc:   # a check that cannot run is an ALARM, never a silent pass
                ok, detail = False, "check could not run: %s" % exc
            alarms += 0 if ok else 1
            print("%-6s %-26s %s" % ("PASS" if ok else "ALARM", name, detail))
        print("INFO   %-26s %s" % ("flight snapshot coverage", info_snapshot_hours(db_con, now)))
    finally:
        for con in (log_con, db_con):
            if con is not None:
                con.close()
    print("%d ALARM(s)" % alarms if alarms else "all checks passed")
    return 1 if alarms else 0


if __name__ == "__main__":
    sys.exit(main())
