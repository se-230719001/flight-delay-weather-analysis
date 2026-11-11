"""M4 helpers for the exploration notebook. Reading only: nothing here writes to the database.

The notebook imports these so that every cell uses the same read-only connection, the same
parameterised queries, the same clock conversion and the same plot style.
"""

import hashlib
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "flights.db"
QUERY_DIR = ROOT / "sql" / "queries"
FIGURE_DIR = ROOT / "figures"
LOCAL_TZ = "Europe/Istanbul"   # UTC+3 all year (no daylight saving since 2016)

# Fixed SQL for the fingerprint: one statement per table, written out so that no name is
# ever formatted into SQL. ORDER BY makes the hash independent of storage order.
FINGERPRINT_SQL = {
    "aircraft": "SELECT * FROM aircraft ORDER BY aircraft_key",
    "aircraft_state": "SELECT * FROM aircraft_state ORDER BY aircraft_key, time_position",
    "location": "SELECT * FROM location ORDER BY location_key",
    "weather_hourly": "SELECT * FROM weather_hourly ORDER BY location_key, observed_at",
    "quarantine": "SELECT * FROM quarantine ORDER BY quarantine_id",
    "location_airport": "SELECT * FROM location_airport ORDER BY location_key",
    "aircraft_state_clean": "SELECT * FROM aircraft_state_clean ORDER BY aircraft_key, time_position",
    "weather_hourly_clean": "SELECT * FROM weather_hourly_clean ORDER BY location_key, observed_at",
}


# --- connection and queries ----------------------------------------------------

def connect_ro(path=None):
    """Open the database read-only: any write raises sqlite3.OperationalError."""
    path = Path(path) if path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run load.py and clean.py first")
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    con.row_factory = sqlite3.Row
    return con


def load_sql(name):
    """Read sql/queries/<name>.sql (name without the extension) as fixed text."""
    return (QUERY_DIR / f"{name}.sql").read_text(encoding="utf-8")


def query_df(con, name, params=None):
    """Run a saved query with bound parameters (a dict for :name placeholders)."""
    cur = con.execute(load_sql(name), params or {})
    columns = [d[0] for d in cur.description]
    return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=columns)


def flag_params(flags):
    """Turn a tuple of up to three allowed flags into {:f1, :f2, :f3}; unused slots are NULL."""
    flags = tuple(flags)
    if not 1 <= len(flags) <= 3:
        raise ValueError("give between one and three flags")
    padded = flags + (None,) * (3 - len(flags))
    return {"f1": padded[0], "f2": padded[1], "f3": padded[2]}


# --- clock ---------------------------------------------------------------------

def to_local(values, tz=LOCAL_TZ):
    """UTC ISO text ('2026-10-19T00:00Z' or '...T10:07:07Z') -> tz-aware local timestamps."""
    return pd.to_datetime(values, utc=True, format="ISO8601").dt.tz_convert(tz)


# --- coverage ------------------------------------------------------------------

def coverage_table(df, by, observed_col, expected, min_share):
    """Observations per group against how many there could have been.

    n = non-null values of observed_col; expected is the number that could exist per group
    (an int, or a Series indexed like the groups); meets_rule says share >= min_share.
    """
    grouped = df.groupby(by)[observed_col].agg(n="count", rows="size")
    grouped["expected"] = expected
    grouped["share"] = (grouped["n"] / grouped["expected"]).round(3)
    grouped["meets_rule"] = grouped["share"] >= min_share
    return grouped.reset_index()


# --- uncertainty ---------------------------------------------------------------

def bootstrap_diff(df, unit, value, group, n_boot=5000, seed=0, level=0.95):
    """Difference in group means with a percentile interval, resampling whole units.

    unit  : column naming the independent unit (for example the local date)
    value : numeric column
    group : column with exactly two labels; the difference is mean(second) - mean(first),
            in sorted label order
    Rows are first averaged per unit and group, so a unit with many rows counts once.
    Returns (difference, lower, upper, n_units).
    """
    labels = sorted(df[group].dropna().unique())
    if len(labels) != 2:
        raise ValueError(f"{group} must have exactly two labels, found {labels}")
    per = df.dropna(subset=[value]).groupby([unit, group])[value].mean().unstack(group)
    per = per.reindex(columns=labels)
    units = per.index.to_numpy()
    a, b = per[labels[0]].to_numpy(), per[labels[1]].to_numpy()

    def diff(idx):
        sa, sb = a[idx], b[idx]
        if np.all(np.isnan(sa)) or np.all(np.isnan(sb)):
            return np.nan
        return np.nanmean(sb) - np.nanmean(sa)

    point = diff(np.arange(len(units)))
    rng = np.random.default_rng(seed)
    draws = np.array([diff(rng.integers(0, len(units), len(units))) for _ in range(n_boot)])
    draws = draws[~np.isnan(draws)]
    if len(draws) == 0:
        return point, np.nan, np.nan, len(units)
    tail = (1 - level) / 2
    lo, hi = np.quantile(draws, [tail, 1 - tail])
    return float(point), float(lo), float(hi), len(units)


def correlation(x, y, method="pearson"):
    """Pearson or Spearman correlation of two columns; NaN when it is undefined.

    Rows with a missing value in either column are dropped. Spearman is Pearson on average ranks.
    """
    if method not in ("pearson", "spearman"):
        raise ValueError(f"method must be 'pearson' or 'spearman', not {method!r}")
    d = pd.DataFrame({"x": np.asarray(x, dtype=float), "y": np.asarray(y, dtype=float)}).dropna()
    if len(d) < 3 or d.x.nunique() < 2 or d.y.nunique() < 2:
        return float("nan")
    if method == "spearman":
        d = d.rank()
    return float(np.corrcoef(d.x, d.y)[0, 1])


def bootstrap_corr(df, unit, x, y, method="pearson", n_boot=5000, seed=0, level=0.95):
    """Correlation with a percentile interval, resampling whole units (for example local days).

    Returns (correlation, lower, upper, n_units). Draws in which the correlation is undefined
    (a constant column) are skipped; the interval is NaN when none is left.
    """
    sub = df.dropna(subset=[x, y])
    parts = [g[[x, y]].to_numpy(dtype=float) for _, g in sub.groupby(unit)]
    point = correlation(sub[x], sub[y], method)
    if not parts:
        return point, float("nan"), float("nan"), 0
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(parts), len(parts))
        s = np.concatenate([parts[i] for i in pick])
        r = correlation(s[:, 0], s[:, 1], method)
        if not np.isnan(r):
            draws.append(r)
    if not draws:
        return point, float("nan"), float("nan"), len(parts)
    tail = (1 - level) / 2
    lo, hi = np.quantile(draws, [tail, 1 - tail])
    return float(point), float(lo), float(hi), len(parts)


# --- reproducibility -----------------------------------------------------------

def db_fingerprint(con):
    """SHA-256 of the full ordered contents of every table: {table: hexdigest}.

    Run before and after the notebook; the two dicts must be equal (M4 section 3.1).
    """
    result = {}
    for table, sql in FINGERPRINT_SQL.items():
        h = hashlib.sha256()
        for row in con.execute(sql):
            h.update(repr(tuple(row)).encode("utf-8"))
        result[table] = h.hexdigest()
    return result


# --- figures -------------------------------------------------------------------

def save_fig(fig, name, caption, figure_dir=None, top=1.0):
    """Write figures/<name>.png with the caption (n, flag filter, clock) printed on the figure.

    Metadata is stripped so that the same data gives byte-identical files.
    """
    figure_dir = Path(figure_dir) if figure_dir else FIGURE_DIR
    figure_dir.mkdir(parents=True, exist_ok=True)
    fig.text(0.01, 0.005, caption, ha="left", va="bottom", fontsize=8, color="#444444", wrap=True)
    fig.tight_layout(rect=(0, 0.06, 1, top))
    path = figure_dir / f"{name}.png"
    fig.savefig(path, dpi=150, metadata={"Software": None})
    return path


# --- M4 phase 2: analysis helpers (pure pandas, no database access) -----------------

def gust_cutoffs(weather, q, col="wind_gusts_10m", by="iata"):
    """Per-airport gust cut-off: the q-quantile of all trusted hours of that airport.

    Computed from the whole weather series, never from the traffic rows, so the definition
    of 'windy' cannot depend on the outcome it is later compared with.
    """
    if not 0 < q < 1:
        raise ValueError("q must be between 0 and 1 (exclusive)")
    return weather.dropna(subset=[col]).groupby(by)[col].quantile(q)


def join_traffic_weather(traffic, weather):
    """LEFT-join the traffic grid to weather on (iata, hour_utc == observed_at).

    Every grid row survives exactly once: an hour without weather gets has_weather = False and
    gust_usable = 0 instead of disappearing. Raises ValueError if the weather frame is not
    unique per (iata, observed_at) (that would multiply grid rows) or if a column name other
    than iata exists on both sides (pandas would silently rename them with _x / _y).
    """
    key = ["iata", "observed_at"]
    clash = (set(traffic.columns) & set(weather.columns)) - {"iata"}
    if clash:
        raise ValueError(f"columns exist on both sides, rename or drop them first: {sorted(clash)}")
    if weather.duplicated(key).any():
        raise ValueError("weather must have one row per (iata, observed_at)")
    merged = traffic.merge(weather, how="left", left_on=["iata", "hour_utc"],
                           right_on=key, validate="many_to_one")
    if len(merged) != len(traffic):
        raise ValueError("join changed the number of traffic rows")
    merged["has_weather"] = merged["observed_at"].notna()
    merged["gust_usable"] = merged["gust_usable"].fillna(0).astype(int)
    return merged


def add_airport_anomaly(df, cols, by="iata", suffix="_anom"):
    """Add <col><suffix> = value minus the mean of that airport (NaN stays NaN).

    Airports differ by a factor of ten in traffic, so raw counts would compare airports,
    not weather. The mean is taken over every row of df, so call this BEFORE any
    filtering on weather.
    """
    out = df.copy()
    for c in cols:
        out[c + suffix] = out[c] - out.groupby(by)[c].transform("mean")
    return out


def split_by_threshold(df, col, cutoff, by="iata", labels=("calm", "windy")):
    """Add a two-label `group` column: labels[1] when col >= cutoff, else labels[0].

    cutoff is a number or a Series indexed by the values of `by` (per-airport cut-offs).
    Rows where col or the cut-off is missing get no label (NaN), so they can be dropped
    explicitly instead of silently counting as calm.
    """
    out = df.copy()
    if isinstance(cutoff, pd.Series):
        thr = out[by].map(cutoff)
    else:
        thr = pd.Series(float(cutoff), index=out.index)
    missing = out[col].isna() | thr.isna()
    group = pd.Series(np.where(out[col] >= thr, labels[1], labels[0]), index=out.index)
    out["group"] = group.where(~missing)
    return out


def format_diff(result, min_units=8):
    """One readable line for a bootstrap_diff result, with the two warnings that matter."""
    diff, lo, hi, n = result
    if np.isnan(lo):
        return f"difference {diff:+.2f}, interval unavailable (n = {n} days)"
    text = f"difference {diff:+.2f}, 95% interval [{lo:+.2f}, {hi:+.2f}] (n = {n} days)"
    if lo <= 0 <= hi:
        text += "; interval includes 0"
    if n < min_units:
        text += f"; fewer than {min_units} days, exploratory only"
    return text


def assert_unchanged(before, after):
    """Raise AssertionError naming every table whose fingerprint differs."""
    changed = sorted(t for t in set(before) | set(after) if before.get(t) != after.get(t))
    if changed:
        raise AssertionError(f"database changed during exploration: {', '.join(changed)}")
