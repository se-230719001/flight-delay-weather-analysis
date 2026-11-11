"""Phase 1 tests for src/explore.py and sql/queries/02-06. Run: python -m unittest discover tests"""

import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import db  # noqa: E402
import explore  # noqa: E402

PARAMS_TRAFFIC = {"radius_deg": 0.25, "lon_scale": 0.76}


def make_db(path):
    """A tiny database with the real schemas: two airports 0.4 deg apart in longitude."""
    con = db.connect(path)
    db.init_schema(con)
    db.init_clean_schema(con)
    with con:
        con.executemany("INSERT INTO location VALUES (?,?,?,?,?,?)", [
            (1, 0, 41.0, 29.0, 100.0, "2026-10-20T10:00:00Z"),
            (2, 1, 41.0, 29.4, 100.0, "2026-10-20T10:00:00Z"),
        ])
        con.executemany("INSERT INTO location_airport VALUES (?,?,?,?,?,?,?)", [
            (1, 0, "AAA", "Airport A", "t", "2026-10-20", "n"),
            (2, 1, "BBB", "Airport B", "t", "2026-10-20", "n"),
        ])
        con.executemany("INSERT INTO aircraft VALUES (?,?,?,?)",
                        [(k, f"00000{k}", "X", "2026-10-20T10:00:00Z") for k in range(1, 6)])
    return con


def add_state(con, key, snapshot, lat, lon, on_ground=0, velocity=200.0, vflag="ok", latflag="ok"):
    con.execute(
        """INSERT INTO aircraft_state_clean VALUES
           (?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?)""",
        (key, f"t{key}-{snapshot}", on_ground,
         lat, lat, latflag, lon, lon, "ok",
         1000.0, 1000.0, "ok", 1000.0, 1000.0, "ok",
         velocity, velocity, vflag, 0.0, 0.0, "ok",
         "ABC", "ABC", "ok", "1000", "1000", "ok", snapshot))


def add_weather(con, loc, hour, gust, flag, retrieved):
    con.execute(
        """INSERT INTO weather_hourly_clean VALUES (?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?, ?)""",
        (loc, hour, 10.0, 10.0, "ok", gust, gust, flag, 0.0, 0.0, "ok", 0.0, 0.0, "ok",
         0.0, 0.0, "ok", retrieved))


class TestConnection(unittest.TestCase):
    def test_read_only_blocks_every_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.db"
            make_db(path).close()
            ro = explore.connect_ro(path)
            self.addCleanup(ro.close)
            for sql in ("CREATE TABLE x (a)", "DELETE FROM location",
                        "UPDATE location SET latitude = 0", "DROP TABLE location"):
                with self.assertRaises(sqlite3.OperationalError, msg=sql):
                    ro.execute(sql)

    def test_missing_database_has_clear_error(self):
        with self.assertRaises(FileNotFoundError):
            explore.connect_ro(Path(tempfile.gettempdir()) / "no_such_dir" / "x.db")


class TestTrafficGrid(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "t.db"
        self.con = make_db(self.path)

    def tearDown(self):
        self.con.close()
        self._tmp.cleanup()

    def run_grid(self):
        return explore.query_df(self.con, "02_snapshot_traffic", PARAMS_TRAFFIC)

    def test_grid_has_every_snapshot_airport_pair_including_zeros(self):
        s1, s2 = "2026-10-20T10:07:07Z", "2026-10-20T11:30:00Z"
        with self.con:
            add_state(self.con, 1, s1, 41.0, 29.0)      # at A in snapshot 1
            add_state(self.con, 2, s2, 41.0, 29.4)      # at B in snapshot 2
        grid = self.run_grid()
        self.assertEqual(len(grid), 4)                   # 2 snapshots x 2 airports
        zero = grid[(grid.snapshot == s1) & (grid.iata == "BBB")].iloc[0]
        self.assertEqual(zero.n_aircraft, 0)
        self.assertTrue(pd.isna(zero.mean_velocity_airborne))

    def test_overlap_state_counted_once_for_nearest_airport(self):
        s = "2026-10-20T10:07:07Z"
        with self.con:
            add_state(self.con, 1, s, 41.0, 29.19)   # inside both areas, nearer A
            add_state(self.con, 2, s, 41.0, 29.21)   # inside both areas, nearer B
        grid = self.run_grid().set_index("iata")
        self.assertEqual(grid.loc["AAA", "n_aircraft"], 1)
        self.assertEqual(grid.loc["BBB", "n_aircraft"], 1)
        self.assertEqual(grid.n_aircraft.sum(), 2)       # never 4

    def test_ground_and_airborne_are_separated(self):
        s = "2026-10-20T10:07:07Z"
        with self.con:
            add_state(self.con, 1, s, 41.0, 29.0, on_ground=0, velocity=200.0)
            add_state(self.con, 2, s, 41.0, 29.0, on_ground=1, velocity=5.0)
        row = self.run_grid().set_index("iata").loc["AAA"]
        self.assertEqual((row.n_aircraft, row.n_airborne, row.n_ground), (2, 1, 1))
        self.assertEqual(row.mean_velocity_airborne, 200.0)   # ground speed not averaged in

    def test_state_outside_radius_and_untrusted_velocity(self):
        s = "2026-10-20T10:07:07Z"
        with self.con:
            add_state(self.con, 1, s, 41.0, 29.0, velocity=None, vflag="invalid_range")
            add_state(self.con, 2, s, 42.0, 29.0)         # 1 degree north: outside every area
        row = self.run_grid().set_index("iata").loc["AAA"]
        self.assertEqual(row.n_aircraft, 1)
        self.assertEqual(row.n_velocity_airborne, 0)      # counted, but velocity not trusted

    def test_untrusted_position_is_left_out(self):
        s = "2026-10-20T10:07:07Z"
        with self.con:
            add_state(self.con, 1, s, 41.0, 29.0)
            add_state(self.con, 2, s, 41.0, 29.0, latflag="invalid_range")
        self.assertEqual(self.run_grid().set_index("iata").loc["AAA", "n_aircraft"], 1)

    def test_hour_key_joins_weather(self):
        with self.con:
            add_state(self.con, 1, "2026-10-20T10:59:59Z", 41.0, 29.0)
        self.assertEqual(self.run_grid().hour_utc.iloc[0], "2026-10-20T10:00Z")


class TestWeatherQueries(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.con = make_db(Path(self._tmp.name) / "t.db")
        retrieved = "2026-10-20T10:07:07Z"
        with self.con:
            add_weather(self.con, 1, "2026-10-20T08:00Z", 20.0, "ok", retrieved)
            add_weather(self.con, 1, "2026-10-20T09:00Z", 40.0, "unusual_high", retrieved)
            add_weather(self.con, 1, "2026-10-20T10:00Z", 21.0, "ok", retrieved)
            add_weather(self.con, 1, "2026-10-20T11:00Z", 22.0, "ok", retrieved)   # forecast
            add_weather(self.con, 1, "2026-10-20T07:00Z", None, "stuck_value", retrieved)

    def tearDown(self):
        self.con.close()
        self._tmp.cleanup()

    def test_forecast_hours_are_excluded(self):
        df = explore.query_df(self.con, "03_weather_hours_clean", explore.flag_params(("ok",)))
        self.assertEqual(len(df), 4)
        self.assertNotIn("2026-10-20T11:00Z", set(df.observed_at))
        self.assertIn("2026-10-20T10:00Z", set(df.observed_at))   # hour 10:00 vs retrieved 10:07:07

    def test_flag_filter_keeps_rows_but_nulls_values(self):
        only_ok = explore.query_df(self.con, "03_weather_hours_clean", explore.flag_params(("ok",)))
        both = explore.query_df(self.con, "03_weather_hours_clean",
                                explore.flag_params(("ok", "unusual_high")))
        self.assertEqual(len(only_ok), len(both))                    # denominators identical
        self.assertEqual(int(only_ok.gust_usable.sum()), 2)
        self.assertEqual(int(both.gust_usable.sum()), 3)
        self.assertEqual(only_ok.wind_gusts_10m.notna().sum(), 2)    # 40.0 hidden under 'ok' only
        self.assertTrue(set(only_ok.dropna(subset=["wind_gusts_10m"]).wind_gusts_10m) <=
                        set(both.dropna(subset=["wind_gusts_10m"]).wind_gusts_10m))

    def test_coverage_query(self):
        cov = explore.query_df(self.con, "04_coverage_weather", explore.flag_params(("ok",)))
        row = cov.iloc[0]
        self.assertEqual((row.iata, row.utc_date, row.n_hours, row.n_gust_usable),
                         ("AAA", "2026-10-20", 4, 2))

    def test_flag_params_validation(self):
        self.assertEqual(explore.flag_params(("ok",)), {"f1": "ok", "f2": None, "f3": None})
        with self.assertRaises(ValueError):
            explore.flag_params(())
        with self.assertRaises(ValueError):
            explore.flag_params(("a", "b", "c", "d"))


class TestHelpers(unittest.TestCase):
    def test_to_local_shifts_three_hours_and_crosses_midnight(self):
        s = pd.Series(["2026-10-19T00:00Z", "2026-10-20T22:30:00Z"])
        local = explore.to_local(s)
        self.assertEqual(local.iloc[0].strftime("%Y-%m-%d %H:%M"), "2026-10-19 03:00")
        self.assertEqual(local.iloc[1].strftime("%Y-%m-%d %H:%M"), "2026-10-21 01:30")
        self.assertEqual(str(local.dt.tz), "Europe/Istanbul")

    def test_coverage_table(self):
        df = pd.DataFrame({"iata": ["A"] * 3 + ["B"], "x": [1, None, 3, 4]})
        cov = explore.coverage_table(df, ["iata"], "x", expected=4, min_share=0.5).set_index("iata")
        self.assertEqual(cov.loc["A", "n"], 2)
        self.assertEqual(cov.loc["A", "rows"], 3)
        self.assertTrue(cov.loc["A", "meets_rule"])
        self.assertFalse(cov.loc["B", "meets_rule"])

    def test_bootstrap_is_reproducible_and_brackets_known_difference(self):
        days = [f"d{i}" for i in range(12)]
        rows = [(d, g, 10.0 + (3.0 if g == "windy" else 0.0) + 0.1 * i)
                for i, d in enumerate(days) for g in ("calm", "windy") for _ in range(4)]
        df = pd.DataFrame(rows, columns=["day", "grp", "v"])
        r1 = explore.bootstrap_diff(df, "day", "v", "grp", n_boot=500, seed=1)
        r2 = explore.bootstrap_diff(df, "day", "v", "grp", n_boot=500, seed=1)
        self.assertEqual(r1, r2)
        diff, lo, hi, n = r1
        self.assertAlmostEqual(diff, 3.0)
        self.assertLessEqual(lo, diff)
        self.assertGreaterEqual(hi, diff)
        self.assertEqual(n, 12)

    def test_bootstrap_resamples_days_not_rows(self):
        # One day with 1000 rows must not out-weigh a day with 1 row.
        df = pd.DataFrame({"day": ["a"] * 1000 + ["b"], "grp": ["x"] * 500 + ["y"] * 500 + ["y"],
                           "v": [0.0] * 500 + [10.0] * 500 + [20.0]})
        diff, _, _, n = explore.bootstrap_diff(df, "day", "v", "grp", n_boot=50, seed=0)
        self.assertEqual(n, 2)
        self.assertAlmostEqual(diff, 15.0)    # mean(y per day)=(10+20)/2, x only on day a (0)

    def test_bootstrap_needs_two_groups(self):
        df = pd.DataFrame({"day": ["a", "b"], "grp": ["x", "x"], "v": [1.0, 2.0]})
        with self.assertRaises(ValueError):
            explore.bootstrap_diff(df, "day", "v", "grp")

    def test_fingerprint_detects_a_change_and_is_otherwise_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.db"
            con = make_db(path)
            self.addCleanup(con.close)
            before = explore.db_fingerprint(con)
            self.assertEqual(before, explore.db_fingerprint(con))
            self.assertEqual(set(before), set(explore.FINGERPRINT_SQL))
            with con:
                con.execute("UPDATE location SET elevation = 999 WHERE location_key = 1")
            after = explore.db_fingerprint(con)
            con.close()
            changed = {t for t in before if before[t] != after[t]}
            self.assertEqual(changed, {"location"})

    def test_save_fig_is_byte_identical_for_same_data(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        with tempfile.TemporaryDirectory() as tmp:
            hashes = []
            for _ in range(2):
                fig, ax = plt.subplots(figsize=(4, 3))
                ax.plot([1, 2, 3], [1, 4, 9])
                p = explore.save_fig(fig, "t", "n = 3; flag filter: ok; clock: UTC", figure_dir=tmp)
                plt.close(fig)
                hashes.append(p.read_bytes())
            self.assertEqual(hashes[0], hashes[1])


class TestRepositoryRules(unittest.TestCase):
    """M4 pass/fail rule: no value is ever formatted into SQL, anywhere in the repository."""

    EXEC = re.compile(r"\.(execute|executemany|executescript)\(\s*(f[\"']|[\"'][^\"']*[\"']\s*(%|\.format|\+))")

    def test_no_formatted_sql_in_python(self):
        offenders = []
        for path in list((ROOT / "src").glob("*.py")) + list((ROOT / "tests").glob("*.py")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.EXEC.search(line):
                    offenders.append(f"{path.name}:{n}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_sql_files_use_only_named_parameters(self):
        for path in (ROOT / "sql" / "queries").glob("*.sql"):
            code = "\n".join(l.split("--")[0] for l in path.read_text(encoding="utf-8").splitlines())
            self.assertNotRegex(code, r"\{|\}|%s|%\(", msg=path.name)


@unittest.skipUnless(explore.DB_PATH.exists(), "real database not built")
class TestRealDatabase(unittest.TestCase):
    """Sanity checks on the real, fully built database (skipped on a fresh clone without it)."""

    @classmethod
    def setUpClass(cls):
        cls.con = explore.connect_ro()
        cls.addClassCleanup(cls.con.close)

    def test_grid_is_snapshots_times_airports(self):
        grid = explore.query_df(self.con, "02_snapshot_traffic", PARAMS_TRAFFIC)
        n_snap = self.con.execute("SELECT COUNT(DISTINCT retrieved_at) FROM aircraft_state_clean").fetchone()[0]
        n_loc = self.con.execute("SELECT COUNT(*) FROM location_airport").fetchone()[0]
        self.assertEqual(len(grid), n_snap * n_loc)
        self.assertEqual(grid.duplicated(["snapshot", "iata"]).sum(), 0)

    def test_no_state_is_double_counted(self):
        grid = explore.query_df(self.con, "02_snapshot_traffic", PARAMS_TRAFFIC)
        ceiling = self.con.execute("SELECT COUNT(*) FROM aircraft_state_clean").fetchone()[0]
        self.assertLessEqual(int(grid.n_aircraft.sum()), ceiling)

    def test_forecast_hours_removed(self):
        df = explore.query_df(self.con, "03_weather_hours_clean", explore.flag_params(("ok",)))
        total = self.con.execute("SELECT COUNT(*) FROM weather_hourly_clean").fetchone()[0]
        forecast = self.con.execute(
            "SELECT COUNT(*) FROM weather_hourly_clean WHERE datetime(observed_at) > datetime(retrieved_at)"
        ).fetchone()[0]
        self.assertEqual(len(df), total - forecast)

    def test_reading_does_not_change_the_database(self):
        before = explore.db_fingerprint(self.con)
        explore.query_df(self.con, "02_snapshot_traffic", PARAMS_TRAFFIC)
        explore.query_df(self.con, "03_weather_hours_clean", explore.flag_params(("ok",)))
        explore.query_df(self.con, "04_coverage_weather", explore.flag_params(("ok",)))
        self.assertEqual(before, explore.db_fingerprint(self.con))


def add_raw_weather(con, loc, hour, gust, retrieved):
    con.execute("INSERT INTO weather_hourly VALUES (?,?,?,?,?,?,?,?)",
                (loc, hour, 10.0, gust, 0.0, 0.0, 0.0, retrieved))


class TestRawVersusCleanQueries(unittest.TestCase):
    """Queries 05 (raw M2 table) and 06 (cleaned layer): the 2.1 key number."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.con = make_db(Path(self._tmp.name) / "t.db")
        retrieved = "2026-10-20T10:07:07Z"
        hours = [("2026-10-20T08:00Z", 20.0, "ok"), ("2026-10-20T09:00Z", 40.0, "unusual_high"),
                 ("2026-10-20T10:00Z", 30.0, "ok"), ("2026-10-20T11:00Z", 90.0, "ok")]   # last one: forecast
        with self.con:
            for hour, gust, flag in hours:
                add_raw_weather(self.con, 1, hour, gust, retrieved)
                add_weather(self.con, 1, hour, gust, flag, retrieved)

    def tearDown(self):
        self.con.close()
        self._tmp.cleanup()

    def overall(self, df):
        return df.set_index("iata").loc["ALL"]

    def test_raw_keeps_every_hour_and_every_value(self):
        raw = self.overall(explore.query_df(self.con, "05_gust_mean_raw", {"observed_only": 0}))
        self.assertEqual((raw.n_hours, raw.n_gust, raw.mean_gust_kmh), (4, 4, 45.0))   # (20+40+30+90)/4

    def test_raw_without_forecast_hours(self):
        obs = self.overall(explore.query_df(self.con, "05_gust_mean_raw", {"observed_only": 1}))
        self.assertEqual((obs.n_hours, obs.mean_gust_kmh), (3, 30.0))                  # (20+40+30)/3

    def test_clean_filter_changes_the_mean_and_keeps_the_denominator(self):
        strict = self.overall(explore.query_df(self.con, "06_gust_mean_clean", explore.flag_params(("ok",))))
        wide = self.overall(explore.query_df(self.con, "06_gust_mean_clean",
                                             explore.flag_params(("ok", "unusual_high"))))
        self.assertEqual((strict.n_hours, strict.n_gust, strict.mean_gust_kmh), (3, 2, 25.0))
        self.assertEqual((wide.n_hours, wide.n_gust, wide.mean_gust_kmh), (3, 3, 30.0))

    def test_per_airport_row_and_no_formatted_values_in_sql(self):
        df = explore.query_df(self.con, "05_gust_mean_raw", {"observed_only": 0})
        self.assertEqual(sorted(df.iata), ["AAA", "ALL"])
        for name in ("05_gust_mean_raw", "06_gust_mean_clean"):
            sql = explore.load_sql(name)
            self.assertNotIn("{", sql)
            self.assertNotIn("%s", sql)


class TestCorrelation(unittest.TestCase):
    def test_pearson_and_spearman_on_a_monotone_but_curved_relation(self):
        x = list(range(1, 11))
        y = [v ** 3 for v in x]
        self.assertAlmostEqual(explore.correlation(x, x), 1.0)
        self.assertAlmostEqual(explore.correlation(x, y, "spearman"), 1.0)
        self.assertLess(explore.correlation(x, y, "pearson"), 1.0)

    def test_undefined_cases_are_nan(self):
        self.assertTrue(np.isnan(explore.correlation([1, 1, 1, 1], [1, 2, 3, 4])))
        self.assertTrue(np.isnan(explore.correlation([1, 2], [1, 2])))
        self.assertTrue(np.isnan(explore.correlation([1, 2, None, 4, 5], [1, 2, 3, None, None])))

    def test_unknown_method_raises(self):
        with self.assertRaises(ValueError):
            explore.correlation([1, 2, 3], [1, 2, 3], "kendall")

    def test_bootstrap_corr_is_reproducible_and_counts_units(self):
        rng = np.random.default_rng(0)
        df = pd.DataFrame({"day": np.repeat(list("abcdefgh"), 5), "x": rng.normal(size=40)})
        df["y"] = df.x * 2 + rng.normal(scale=0.1, size=40)
        r1 = explore.bootstrap_corr(df, "day", "x", "y", n_boot=200, seed=3)
        r2 = explore.bootstrap_corr(df, "day", "x", "y", n_boot=200, seed=3)
        self.assertEqual(r1, r2)
        r, lo, hi, n = r1
        self.assertEqual(n, 8)
        self.assertGreater(r, 0.95)
        self.assertLessEqual(lo, r + 1e-9)
        self.assertGreaterEqual(hi, r - 1e-9)


if __name__ == "__main__":
    unittest.main()
