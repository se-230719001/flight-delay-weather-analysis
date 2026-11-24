"""Drill 6: an old raw file is re-loaded AFTER a newer version of the same record.

    python ops/drill_old_file.py

Works on temporary copies: your real database and raw files are not touched. It finds an
Open-Meteo hour whose value was revised between the oldest and the newest landed file,
loads the newest file into a scratch database, then loads the OLDEST file into the same
database (what a backfill re-reading an old file does). The guarded upsert must keep the
newer value. Exit 0 = the newer value stayed, 1 = it was overwritten (or no revised hour).
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import load  # noqa: E402

RAW = ROOT / "data" / "raw" / "openmeteo"


def hour_values(path):
    """{(location index, hour): wind_speed_10m} for one raw file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for i, loc in enumerate(data if isinstance(data, list) else [data]):
        for t, v in zip(loc["hourly"]["time"], loc["hourly"]["wind_speed_10m"]):
            out[(i, t)] = v
    return out


def main():
    files = sorted(RAW.glob("*.json"))   # names start with the retrieval stamp
    seen = {f: hour_values(f) for f in files}
    for old in files:                       # oldest first
        for new in reversed(files):         # newest first
            if old == new:
                continue
            for key, v_old in seen[old].items():
                v_new = seen[new].get(key)
                if v_old is not None and v_new is not None and v_old != v_new:
                    return drill(old, new, key, v_old, v_new)
    print("DRILL INCONCLUSIVE: no hour was revised between two landed files yet")
    return 1


def drill(old, new, key, v_old, v_new):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name, src in (("newer", new), ("older", old)):
            (tmp / name / "openmeteo").mkdir(parents=True)
            (tmp / name / "opensky").mkdir(parents=True)
            shutil.copy(src, tmp / name / "openmeteo" / src.name)
        db = tmp / "scratch.db"
        sql = ("SELECT wind_speed_10m FROM weather_hourly WHERE observed_at = ? "
               "ORDER BY location_key")
        hour = key[1].replace("T", "T") + "Z"
        load.run(db_path=db, raw_dir=tmp / "newer")
        con = sqlite3.connect(db)
        before = [r[0] for r in con.execute(sql, (hour,))]
        con.close()
        load.run(db_path=db, raw_dir=tmp / "older")      # the old file arrives later
        con = sqlite3.connect(db)
        after = [r[0] for r in con.execute(sql, (hour,))]
        con.close()
    print(f"hour {hour}, location #{key[0]}: older file says {v_old}, newer file says {v_new}")
    print(f"values for that hour after loading the newer file: {before}")
    print(f"values for that hour after then loading the older file: {after}")
    if after == before and before[key[0]] == v_new:
        print("DRILL PASSED: the newer value stayed")
        return 0
    print("DRILL FAILED: the old file overwrote a newer value")
    return 1


if __name__ == "__main__":
    sys.exit(main())
