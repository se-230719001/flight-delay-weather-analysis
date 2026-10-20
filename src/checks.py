"""M2 checks: prove the database refuses what it should and the loader behaves.

Run from the repository root:
    python src/checks.py

Everything runs on a temporary copy of the database (and of the raw files);
the real database is never touched.
"""

import contextlib
import io
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import load
from db import DB_PATH, connect

TABLE_SQL = {
    "aircraft": "SELECT * FROM aircraft",
    "aircraft_state": "SELECT * FROM aircraft_state",
    "location": "SELECT * FROM location",
    "weather_hourly": "SELECT * FROM weather_hourly",
    "quarantine": "SELECT * FROM quarantine",
}


def snapshot(db_path):
    """Full content of every table (sorted), for exact before/after comparison."""
    con = connect(db_path)
    try:
        return {t: sorted(map(tuple, con.execute(sql).fetchall()), key=repr)
                for t, sql in TABLE_SQL.items()}
    finally:
        con.close()


def row_counts(snap):
    return {t: len(rows) for t, rows in snap.items()}


def quiet_load(db_path, raw_dir):
    with contextlib.redirect_stdout(io.StringIO()):
        return load.run(db_path, raw_dir)


def rejected(db_path, sql, params):
    """True if SQLite refuses the statement (constraint / type error)."""
    con = connect(db_path)
    try:
        with con:
            con.execute(sql, params)
        return False
    except sqlite3.Error:
        return True
    finally:
        con.close()


def make_bad_raw_copy(raw_dir, tmp):
    """Copy of the raw files plus one new file with a broken record (and a new aircraft)."""
    bad = Path(tmp) / "raw_bad"
    shutil.copytree(raw_dir, bad)
    newest = sorted((bad / "opensky").glob("*.json"))[-1]
    text = newest.read_text(encoding="utf-8")
    import json
    data = json.loads(text)
    data["states"][0][0] = "ffffff"            # a new aircraft, so the run has something to write
    data["states"].append(["broken record"])   # structurally invalid state vector
    (bad / "opensky" / "20991231T000000Z.json").write_text(json.dumps(data), encoding="utf-8")
    return bad


def main():
    raw_dir = load.RAW_DIR
    results = []

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "check.db"
        if Path(DB_PATH).exists():
            shutil.copyfile(DB_PATH, db)
        else:
            quiet_load(db, raw_dir)

        con = connect(db)
        icao = con.execute("SELECT icao24 FROM aircraft LIMIT 1").fetchone()[0]
        aircraft_key = con.execute("SELECT aircraft_key FROM aircraft LIMIT 1").fetchone()[0]
        location_key = con.execute("SELECT location_key FROM location LIMIT 1").fetchone()[0]
        con.close()

        results.append(("Duplicate natural key", rejected(
            db, "INSERT INTO aircraft (icao24, origin_country, retrieved_at) VALUES (?, ?, ?)",
            (icao, "Test", "2026-01-01T00:00:00Z"))))

        results.append(("Orphan event", rejected(
            db, "INSERT INTO aircraft_state (aircraft_key, time_position, retrieved_at) "
                "VALUES (?, ?, ?)", (-1, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))))

        results.append(("NULL key", rejected(
            db, "INSERT INTO aircraft_state (aircraft_key, time_position, retrieved_at) "
                "VALUES (?, ?, ?)", (aircraft_key, None, "2026-01-01T00:00:00Z"))))

        results.append(("Wrong type", rejected(
            db, "INSERT INTO weather_hourly (location_key, observed_at, wind_speed_10m, "
                "retrieved_at) VALUES (?, ?, ?, ?)",
            (location_key, "1999-01-01T00:00Z", "n/a", "2026-01-01T00:00:00Z"))))

        # Idempotence: loading again (twice) must leave every table identical.
        idem_db = Path(tmp) / "idem.db"
        quiet_load(idem_db, raw_dir)
        first = snapshot(idem_db)
        quiet_load(idem_db, raw_dir)
        quiet_load(idem_db, raw_dir)
        second = snapshot(idem_db)
        results.append(("Idempotence", first == second))

        # Atomicity (a): a broken record in the raw data -> nothing written.
        bad_raw = make_bad_raw_copy(raw_dir, tmp)
        atom_db = Path(tmp) / "atom.db"
        shutil.copyfile(idem_db, atom_db)
        before = snapshot(atom_db)
        try:
            quiet_load(atom_db, bad_raw)
            failed_as_expected = False
        except Exception:
            failed_as_expected = True
        after_a = snapshot(atom_db)

        # Atomicity (b): the load fails partway through writing -> rollback.
        good_raw = Path(tmp) / "raw_good"
        shutil.copytree(bad_raw, good_raw)
        (good_raw / "opensky" / "20991231T000000Z.json").unlink()
        newest = sorted((good_raw / "opensky").glob("*.json"))[-1]
        import json
        data = json.loads(newest.read_text(encoding="utf-8"))
        data["states"][0][0] = "ffffff"        # valid file, new aircraft: writes begin
        (good_raw / "opensky" / "20991231T000000Z.json").write_text(json.dumps(data), encoding="utf-8")
        original = load.UPSERT_WEATHER
        load.UPSERT_WEATHER = "INSERT INTO weather_hourly (no_such_column) VALUES (?)"
        try:
            quiet_load(atom_db, good_raw)
            midway_failed = False
        except Exception:
            midway_failed = True
        finally:
            load.UPSERT_WEATHER = original
        after_b = snapshot(atom_db)

        atomic = (failed_as_expected and midway_failed
                  and row_counts(before) == row_counts(after_a) == row_counts(after_b)
                  and before == after_a == after_b)
        results.append(("Atomicity", atomic))

    for name, ok in results:
        print(f"{name:<22} {'PASS' if ok else 'FAIL'}")
    all_ok = all(ok for _, ok in results)
    print("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())