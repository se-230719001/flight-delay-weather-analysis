"""Shared database helpers. Every script connects through connect()."""

import argparse
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "flights.db"
SCHEMA_PATH = ROOT / "sql" / "schema.sql"
CLEAN_SCHEMA_PATH = ROOT / "sql" / "clean_schema.sql"
LOG_DB_PATH = ROOT / "data" / "pipeline_log.db"   # run log: separate from the main database

RUN_TIME_FORMAT = "%Y-%m-%dT%H:%MZ"


def connect(path=None, create=False):
    """Open the database with foreign keys enforced (must be set on every connection).

    A database that should already exist is never created by accident: a wrong path raises
    FileNotFoundError instead of silently giving an empty database. Only load.py (which
    builds the database) and the run log pass create=True.
    """
    path = Path(path) if path else DB_PATH
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.exists():
        raise FileNotFoundError(f"database not found: {path} (run python src/load.py first)")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


# --- The pipeline's clock ---------------------------------------------------------
def parse_run_time(text):
    """'2026-11-20T08:10Z' (or with seconds) -> aware UTC datetime."""
    for fmt in (RUN_TIME_FORMAT, "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    raise ValueError(f"run time must look like 2026-11-20T08:10Z, got {text!r}")


def format_run_time(dt):
    return dt.astimezone(timezone.utc).strftime(RUN_TIME_FORMAT)


def default_run_time():
    """The only place the pipeline's clock reads the wall clock: --run-time defaults to now."""
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def _run_time_arg(text):
    try:
        return parse_run_time(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def add_run_time_argument(parser):
    parser.add_argument("--run-time", type=_run_time_arg, default=None,
                        help="the pipeline's clock, e.g. 2026-11-20T08:10Z (default: now)")


def resolve_run_time(value):
    return value if value is not None else default_run_time()


def init_schema(con):
    """Create all tables if they do not exist yet (idempotent)."""
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    con.execute("PRAGMA foreign_keys = ON")  # executescript must not leave it off


def init_clean_schema(con):
    """Create the M3 cleaned layer if it does not exist yet (idempotent). Needs the M2 schema first."""
    con.executescript(CLEAN_SCHEMA_PATH.read_text(encoding="utf-8"))
    con.execute("PRAGMA foreign_keys = ON")


def epoch_to_iso(value):
    """Unix seconds -> '2026-10-20T10:07:07Z'; None if missing or invalid."""
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def meteo_time_to_iso(value):
    """Open-Meteo '2026-10-19T00:00' (already UTC) -> '2026-10-19T00:00Z'; None if invalid."""
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        return None
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def retrieved_at_from_filename(path):
    """'20261020T100707Z.json' or '20261020T102018Z_1.json' -> '2026-10-20T10:07:07Z'."""
    m = re.match(r"(\d{8}T\d{6}Z)", Path(path).name)
    if not m:
        raise ValueError(f"Unexpected raw file name: {Path(path).name}")
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")