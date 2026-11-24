"""M5 runner: one command that runs ingest -> load -> clean -> validate, honestly.

    python src/run_pipeline.py                          # fetch, load, clean, validate
    python src/run_pipeline.py --skip-ingest            # rebuild from landed raw data (no network)
    python src/run_pipeline.py --run-time 2026-11-20T08:10Z

Rules:
    - every step is a separate process (a crash in a step cannot take the runner down);
    - the run stops at the first failing step and exits with THAT step's exit code;
    - every step has a timeout: a step that does not finish in time is killed, exit code 124;
    - every step that ran (including the failed one) is a row in data/pipeline_log.db;
    - one clock: --run-time (default: now) is passed to every step;
    - one run at a time: a second run exits 75 and runs nothing.

Exit codes: 0 success | 75 another run in progress | 124 a step timed out |
            127 a step could not be started | anything else: the failing step's own code.
"""

import argparse
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

from db import (LOG_DB_PATH, ROOT, add_run_time_argument, connect, format_run_time,
                resolve_run_time)

# (script, timeout in seconds). Ingestion is the slow one: bounded retries + backoff.
STEPS = [
    ("src/ingest.py", 900),
    ("src/load.py", 900),
    ("src/clean.py", 900),
    ("src/validate.py", 600),
]
EXIT_LOCKED = 75
EXIT_TIMEOUT = 124
EXIT_NOT_STARTED = 127

# Drill hooks (docs/pipeline.md, section 4). Both are OFF unless the environment variable is set:
#   PIPELINE_DRILL_HANG=src/clean.py   that step sleeps instead of running (hang drill)
#   PIPELINE_TIMEOUT_OVERRIDE=5        every step gets this many seconds instead
DRILL_HANG_STEP = os.environ.get("PIPELINE_DRILL_HANG")
TIMEOUT_OVERRIDE = os.environ.get("PIPELINE_TIMEOUT_OVERRIDE")

LOCK_PATH = ROOT / "data" / "pipeline.lock"
# A lock older than every timeout added together can only be left over from a crash or a
# shutdown (the machine is switched off every day): it is stale and may be replaced.
# Age is used, not a process id: os.kill(pid, 0) is not a safe "is it alive" test on Windows.
LOCK_MAX_AGE_SECONDS = sum(t for _, t in STEPS) + 600

CREATE_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id      TEXT NOT NULL,   -- one per run
    run_time    TEXT NOT NULL,   -- the pipeline's clock, ISO 8601 UTC
    step        TEXT NOT NULL,   -- e.g. 'src/load.py'
    started_at  TEXT NOT NULL,   -- wall clock, ISO 8601 UTC
    finished_at TEXT NOT NULL,
    exit_code   INTEGER NOT NULL,
    detail      TEXT,            -- the step's summary lines
    PRIMARY KEY (run_id, step)
) STRICT
"""
INSERT_LOG = """
INSERT INTO pipeline_run (run_id, run_time, step, started_at, finished_at, exit_code, detail)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""
DETAIL_KEYWORDS = ("this run", "STATUS", "sources fetched", "checks:", "ERROR", "WARN", "FAILED")


def wall_clock():
    """Wall clock: used ONLY for the started_at / finished_at log timestamps."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Lock ----------------------------------------------------------------------------
def acquire_lock():
    """True if we now hold the lock. False if another run is genuinely in progress."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - LOCK_PATH.stat().st_mtime
            except FileNotFoundError:
                continue   # released between our two calls: try again
            if age < LOCK_MAX_AGE_SECONDS:
                return False
            print(f"Removing a stale lock ({age / 3600:.1f} h old, left by a crash or shutdown).")
            LOCK_PATH.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(f"pid {os.getpid()} since {wall_clock()}\n")
        return True
    return False


def release_lock():
    LOCK_PATH.unlink(missing_ok=True)


# --- Log -----------------------------------------------------------------------------
def log_step(run_id, run_time, step, started_at, finished_at, exit_code, detail):
    con = connect(LOG_DB_PATH, create=True)   # the run log is created on first use
    try:
        with con:
            con.execute(CREATE_LOG_TABLE)
            con.execute(INSERT_LOG, (run_id, run_time, step, started_at, finished_at,
                                     exit_code, detail))
    finally:
        con.close()


def summarize(output, exit_code):
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    picked = [ln for ln in lines if any(k in ln for k in DETAIL_KEYWORDS)]
    if not picked:
        picked = lines[-2:]
    if exit_code != 0:
        picked = picked + lines[-2:]
    seen, unique = set(), []
    for ln in picked:
        if ln not in seen:
            seen.add(ln)
            unique.append(ln)
    return "; ".join(unique)[:600] or None


# --- One step ------------------------------------------------------------------------
def as_text(value):
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def run_step(step, timeout, run_time_text):
    """Run one step as its own process. Returns (exit_code, output)."""
    cmd = [sys.executable, step, "--run-time", run_time_text]
    if DRILL_HANG_STEP == step:
        cmd = [sys.executable, "-c", "import time; time.sleep(3600)"]
    if TIMEOUT_OVERRIDE:
        timeout = float(TIMEOUT_OVERRIDE)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")   # Windows consoles are not UTF-8
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = as_text(exc.stdout) + as_text(exc.stderr)
        return EXIT_TIMEOUT, out + f"\nERROR: {step} did not finish within {timeout} s and was stopped"
    except OSError as exc:
        return EXIT_NOT_STARTED, f"ERROR: could not start {step}: {exc}"
    return proc.returncode, proc.stdout + proc.stderr


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run ingest -> load -> clean -> validate.")
    add_run_time_argument(parser)
    parser.add_argument("--skip-ingest", action="store_true",
                        help="do not fetch: rebuild everything from the landed raw data")
    args = parser.parse_args(argv)

    run_time = resolve_run_time(args.run_time)
    run_time_text = format_run_time(run_time)
    run_id = uuid.uuid4().hex[:8]

    if not acquire_lock():
        print(f"Another pipeline run is in progress ({LOCK_PATH}). Nothing was run.")
        return EXIT_LOCKED

    steps = [s for s in STEPS if not (args.skip_ingest and s[0] == "src/ingest.py")]
    try:
        print(f"Pipeline run {run_id}, run time {run_time_text}"
              + (" (ingestion skipped)" if args.skip_ingest else ""))
        for step, timeout in steps:
            print(f"--- {step} ---", flush=True)
            started_at = wall_clock()
            code, output = run_step(step, timeout, run_time_text)
            finished_at = wall_clock()
            log_step(run_id, run_time_text, step, started_at, finished_at, code,
                     summarize(output, code))
            print(output.rstrip())
            if code != 0:
                print(f"PIPELINE FAILED at {step} (exit code {code}"
                      + (", timeout" if code == EXIT_TIMEOUT else "")
                      + f"). Later steps did not run. Run {run_id}.")
                return code
        print(f"PIPELINE OK: all {len(steps)} steps succeeded. Run {run_id}, run time {run_time_text}.")
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
