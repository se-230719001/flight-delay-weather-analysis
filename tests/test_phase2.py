"""Phase 2 tests: analysis helpers in src/explore.py and the notebook notebooks/m4_eda.ipynb.

Run: python -m unittest discover tests
The notebook is executed cell by cell in plain Python (no Jupyter needed) against the real
database; that test is skipped on a fresh clone without data/flights.db.
"""

import ast
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")   # before pyplot is imported anywhere

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import explore  # noqa: E402

NOTEBOOK = ROOT / "notebooks" / "m4_eda.ipynb"


# --- helpers --------------------------------------------------------------------------

class TestGustCutoffs(unittest.TestCase):
    def test_cutoff_is_per_airport_quantile_of_trusted_hours(self):
        w = pd.DataFrame({"iata": ["A"] * 5 + ["B"] * 5,
                          "wind_gusts_10m": [1, 2, 3, 4, 5, 10, 20, 30, 40, np.nan]})
        cut = explore.gust_cutoffs(w, 0.5)
        self.assertEqual(cut["A"], 3.0)
        self.assertEqual(cut["B"], 25.0)          # NaN ignored: median of 10, 20, 30, 40

    def test_q_must_be_strictly_between_zero_and_one(self):
        w = pd.DataFrame({"iata": ["A"], "wind_gusts_10m": [1.0]})
        for q in (0, 1, -0.1, 1.5):
            with self.assertRaises(ValueError):
                explore.gust_cutoffs(w, q)


class TestJoinTrafficWeather(unittest.TestCase):
    def setUp(self):
        self.traffic = pd.DataFrame({
            "snapshot": ["s1", "s2", "s3"], "iata": ["A", "A", "B"],
            "hour_utc": ["h10", "h11", "h10"], "n_airborne": [3, 0, 5]})
        self.weather = pd.DataFrame({
            "iata": ["A", "B"], "observed_at": ["h10", "h10"],
            "wind_gusts_10m": [20.0, np.nan], "gust_usable": [1, 0]})

    def test_every_traffic_row_survives_once(self):
        j = explore.join_traffic_weather(self.traffic, self.weather)
        self.assertEqual(len(j), 3)
        self.assertEqual(list(j.snapshot), ["s1", "s2", "s3"])

    def test_missing_hour_is_kept_and_marked(self):
        j = explore.join_traffic_weather(self.traffic, self.weather).set_index("snapshot")
        self.assertFalse(j.loc["s2", "has_weather"])      # no weather hour h11 for A
        self.assertEqual(j.loc["s2", "gust_usable"], 0)
        self.assertTrue(j.loc["s3", "has_weather"])       # hour exists, gust not trusted
        self.assertEqual(j.loc["s3", "gust_usable"], 0)
        self.assertEqual(j.loc["s1", "gust_usable"], 1)

    def test_duplicate_weather_key_is_refused(self):
        dup = pd.concat([self.weather, self.weather.iloc[[0]]], ignore_index=True)
        with self.assertRaises(ValueError):
            explore.join_traffic_weather(self.traffic, dup)

    def test_column_clash_is_refused_not_suffixed(self):
        clash = self.weather.assign(local_date="x")
        with self.assertRaises(ValueError):
            explore.join_traffic_weather(self.traffic.assign(local_date="y"), clash)


class TestAnomalyAndSplit(unittest.TestCase):
    def test_anomaly_subtracts_each_airports_own_mean_and_keeps_nan(self):
        df = pd.DataFrame({"iata": ["A", "A", "B", "B"], "v": [1.0, 3.0, 10.0, np.nan]})
        out = explore.add_airport_anomaly(df, ["v"])
        self.assertEqual(list(out.v_anom.iloc[:2]), [-1.0, 1.0])
        self.assertEqual(out.v_anom.iloc[2], 0.0)          # B mean ignores NaN
        self.assertTrue(np.isnan(out.v_anom.iloc[3]))
        self.assertNotIn("v_anom", df.columns)              # input not modified

    def test_split_uses_per_airport_cutoff_and_ge(self):
        df = pd.DataFrame({"iata": ["A", "A", "B"], "g": [5.0, 10.0, 10.0]})
        out = explore.split_by_threshold(df, "g", pd.Series({"A": 10.0, "B": 20.0}))
        self.assertEqual(list(out.group), ["calm", "windy", "calm"])

    def test_split_scalar_cutoff_and_missing_values_get_no_label(self):
        df = pd.DataFrame({"iata": ["A", "A", "B"], "g": [5.0, np.nan, 10.0]})
        out = explore.split_by_threshold(df, "g", 8.0)
        self.assertEqual(out.group.iloc[0], "calm")
        self.assertTrue(pd.isna(out.group.iloc[1]))         # never silently 'calm'
        self.assertEqual(out.group.iloc[2], "windy")

    def test_airport_without_cutoff_gets_no_label(self):
        df = pd.DataFrame({"iata": ["A", "Z"], "g": [5.0, 5.0]})
        out = explore.split_by_threshold(df, "g", pd.Series({"A": 1.0}))
        self.assertEqual(out.group.iloc[0], "windy")
        self.assertTrue(pd.isna(out.group.iloc[1]))


class TestReporting(unittest.TestCase):
    def test_format_diff_flags_zero_and_few_days(self):
        text = explore.format_diff((0.5, -1.0, 2.0, 5), min_units=8)
        self.assertIn("+0.50", text)
        self.assertIn("includes 0", text)
        self.assertIn("fewer than 8 days", text)

    def test_format_diff_clean_result_has_no_warning(self):
        text = explore.format_diff((3.0, 1.0, 5.0, 20), min_units=8)
        self.assertNotIn("includes 0", text)
        self.assertNotIn("fewer than", text)

    def test_format_diff_handles_missing_interval(self):
        self.assertIn("unavailable", explore.format_diff((1.0, float("nan"), float("nan"), 3)))

    def test_assert_unchanged(self):
        explore.assert_unchanged({"a": "1", "b": "2"}, {"a": "1", "b": "2"})
        with self.assertRaises(AssertionError) as ctx:
            explore.assert_unchanged({"a": "1", "b": "2"}, {"a": "1", "b": "X"})
        self.assertIn("b", str(ctx.exception))
        with self.assertRaises(AssertionError):                 # a table that vanished
            explore.assert_unchanged({"a": "1", "b": "2"}, {"a": "1"})


# --- notebook -------------------------------------------------------------------------

def load_notebook():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def code_cells(nb):
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


class TestNotebookStructure(unittest.TestCase):
    def test_is_valid_notebook_json_with_code_and_text(self):
        nb = load_notebook()
        self.assertEqual(nb["nbformat"], 4)
        kinds = {c["cell_type"] for c in nb["cells"]}
        self.assertEqual(kinds, {"markdown", "code"})
        for src in code_cells(nb):
            ast.parse(src)                                      # every code cell is valid Python

    def test_all_data_access_goes_through_the_helpers(self):
        for src in code_cells(load_notebook()):
            self.assertNotIn(".execute(", src)
            self.assertNotIn("import sqlite3", src)
            self.assertNotIn("DELETE", src.upper().replace("DELETED", ""))

    def test_opens_read_only_and_ends_with_the_fingerprint_check(self):
        cells = code_cells(load_notebook())
        self.assertIn("connect_ro(", cells[0])
        self.assertIn("db_fingerprint(", cells[0])
        self.assertIn("assert_unchanged(", cells[-1])

    def test_committed_notebook_has_no_error_outputs(self):
        # M4 requires the notebook to be committed WITH its outputs; an output that is an
        # error means it was saved from a failed run (for example on an empty database).
        for c in load_notebook()["cells"]:
            if c["cell_type"] == "code":
                for out in c["outputs"]:
                    self.assertNotEqual(out.get("output_type"), "error", msg="".join(c["source"])[:60])

    def test_every_figure_goes_through_save_fig(self):
        for src in code_cells(load_notebook()):
            self.assertNotIn("savefig(", src)                   # save_fig adds the caption
            if "plt.subplots" in src and "not saved to figures/" not in src:   # trial plots are marked
                self.assertIn("explore.save_fig(", src)


@unittest.skipUnless(explore.DB_PATH.exists(), "real database not built")
class TestNotebookRuns(unittest.TestCase):
    N_BOOT = "200"

    def run_notebook(self, figure_dir):
        """Execute every code cell in one namespace; return captured stdout."""
        old_cwd, old_boot, old_dir = Path.cwd(), os.environ.get("M4_N_BOOT"), explore.FIGURE_DIR
        os.environ["M4_N_BOOT"] = self.N_BOOT
        explore.FIGURE_DIR = Path(figure_dir)
        os.chdir(NOTEBOOK.parent)
        buf = io.StringIO()
        try:
            ns = {"__name__": "__main__", "display": lambda *args, **kwargs: None}   # Jupyter provides display()
            with contextlib.redirect_stdout(buf):
                for i, src in enumerate(code_cells(load_notebook())):
                    exec(compile(ast.parse(src), f"cell{i}", "exec"), ns)
        finally:
            os.chdir(old_cwd)
            explore.FIGURE_DIR = old_dir
            if old_boot is None:
                os.environ.pop("M4_N_BOOT", None)
            else:
                os.environ["M4_N_BOOT"] = old_boot
            import matplotlib.pyplot as plt
            plt.close("all")
        return buf.getvalue(), ns

    def test_runs_end_to_end_and_leaves_the_database_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, ns = self.run_notebook(tmp)
            self.assertIn("identical before and after", out)
            self.assertEqual(sorted(p.name for p in Path(tmp).glob("*.png")),
                             ["fig01_weather_coverage.png", "fig02_traffic_by_local_hour.png",
                              "fig03_traffic_vs_gust.png", "fig04_windy_vs_calm.png",
                              "fig05_gust_shape.png", "fig06_time_scales.png",
                              "fig07_composition_windy_calm.png"])
        # the traffic grid inside the notebook is complete: snapshots x airports
        traffic = ns["traffic"]
        self.assertEqual(len(traffic), traffic.snapshot.nunique() * traffic.iata.nunique())

    def test_join_keeps_every_grid_row_and_anomalies_centre_on_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, ns = self.run_notebook(tmp)
        self.assertEqual(len(ns["joined"]), len(ns["traffic"]))
        centred = ns["anom"].groupby("iata").n_airborne_anom.mean().abs().max()
        self.assertLess(centred, 1e-9)

    def test_windy_definition_does_not_depend_on_traffic(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, ns = self.run_notebook(tmp)
        w, cut = ns["w"], explore.gust_cutoffs(ns["w"], ns["WINDY_Q"])
        expected = w.dropna(subset=["wind_gusts_10m"]).groupby("iata").wind_gusts_10m.quantile(ns["WINDY_Q"])
        pd.testing.assert_series_equal(cut, expected)

    def test_two_runs_give_byte_identical_figures_and_the_same_numbers(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _, ns1 = self.run_notebook(a)
            _, ns2 = self.run_notebook(b)
            for p in sorted(Path(a).glob("*.png")):
                self.assertEqual(p.read_bytes(), (Path(b) / p.name).read_bytes(), msg=p.name)
        pd.testing.assert_frame_equal(ns1["effect"], ns2["effect"])


if __name__ == "__main__":
    unittest.main()
