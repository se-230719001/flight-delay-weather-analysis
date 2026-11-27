"""Missed-run recovery, shown with completeness BEFORE and AFTER (drill 5).

    python ops/missed_run_demo.py                  # uses the largest gap in the run log
    python ops/missed_run_demo.py --prev 2026-11-23T12:07Z --next 2026-11-24T11:17Z

The machine was off between two runs (PREV and NEXT). Open-Meteo healed the gap in the run
at NEXT (past_days covers the whole gap); OpenSky cannot be healed. This script rebuilds two
SCRATCH databases from the landed raw files (your real database is not touched):
  before = only files landed up to PREV           (what we had when the gap began)
  after  = files landed up to the run at NEXT     (what the schedule recovered)
and runs monitor.py's completeness check on both, with the clock set shortly after NEXT.
"""
import argparse
import contextlib
import io
import shutil
import sqlite3
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import load       # noqa: E402
import monitor    # noqa: E402
from db import LOG_DB_PATH, format_run_time, parse_run_time, retrieved_at_from_filename  # noqa: E402

RAW = ROOT / "data" / "raw"


def largest_gap():
    con = sqlite3.connect(LOG_DB_PATH)
    runs = [parse_run_time(r[0]) for r in con.execute("SELECT DISTINCT run_time FROM pipeline_run ORDER BY run_time")]
    con.close()
    gaps = [(b - a, a, b) for a, b in zip(runs, runs[1:])]
    if not gaps:
        sys.exit("need at least two runs in the run log")
    return max(gaps)[1:]


def build(raw_dir, upto, out_db):
    """Scratch database from the raw files landed at or before `upto`."""
    for source in ("opensky", "openmeteo"):
        (raw_dir / source).mkdir(parents=True)
        for path in (RAW / source).glob("*.json"):
            if retrieved_at_from_filename(path) <= upto:
                shutil.copy(path, raw_dir / source / path.name)
    with contextlib.redirect_stdout(io.StringIO()):   # keep load.py's summary out of the report
        load.run(db_path=out_db, raw_dir=raw_dir)
    return sqlite3.connect(f"file:{out_db.as_posix()}?mode=ro", uri=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev")
    ap.add_argument("--next")
    args = ap.parse_args()
    if args.prev and args.next:
        prev, nxt = parse_run_time(args.prev), parse_run_time(args.next)
    else:
        prev, nxt = largest_gap()
    now = nxt + timedelta(hours=1)
    gap_h = (nxt - prev).total_seconds() / 3600
    print(f"gap: {format_run_time(prev)} -> {format_run_time(nxt)} ({gap_h:.1f} h without a run)")
    print(f"clock for the checks: {format_run_time(now)}\n")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for label, upto in (("BEFORE (files landed up to the last run before the gap)", prev + timedelta(minutes=10)),
                            ("AFTER  (files landed up to the first run after the gap)", nxt + timedelta(minutes=10))):
            name = label.split()[0].lower()
            print(label)
            con = build(tmp / name, upto.strftime("%Y-%m-%dT%H:%M:%SZ"), tmp / (name + ".db"))
            ok, detail = monitor.check_completeness(None, con, now)
            print("  %-5s completeness vs calendar  %s" % ("PASS" if ok else "ALARM", detail))
            print("  INFO  " + monitor.info_snapshot_hours(con, now))
            con.close()
            print()
    print("Weather: healed by the run after the gap. Flights: OpenSky has no history, so the hours")
    print("without a snapshot stay lost (source limit, see docs/pipeline.md).")


if __name__ == "__main__":
    main()
