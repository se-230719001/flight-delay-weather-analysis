"""Shared database helpers. Every script connects through connect()."""

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "flights.db"
SCHEMA_PATH = ROOT / "sql" / "schema.sql"
CLEAN_SCHEMA_PATH = ROOT / "sql" / "clean_schema.sql"


def connect(path=None):
    """Open the database with foreign keys enforced (must be set on every connection)."""
    path = Path(path) if path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


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