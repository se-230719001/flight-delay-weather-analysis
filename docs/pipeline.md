# Pipeline — Flight delay & weather analysis

Everything below describes the code in this repository at the commit tagged `m5`.
Sections marked **(fill in)** need output from your own machine; each says which command produces it.

## 1. The graph

```
 OpenSky API ─┐                                                       ┌─ data/pipeline_log.db (run log)
              ├─> ingest ──> data/raw/ ──> load ──> M2 tables ──> clean ──> M3 tables ──> validate
 Open-Meteo ──┘   (src/ingest.py)         (load.py)  (flights.db)  (clean.py)              (validate.py)
                                                                          monitor.py reads run log + db, read-only
```

| Step | Input | Output | Can be rebuilt from |
|---|---|---|---|
| `src/ingest.py` | OpenSky `/states/all`, Open-Meteo forecast API | one new timestamped JSON per source in `data/raw/<source>/` (never overwritten) | **cannot** be rebuilt: OpenSky has no history. Raw files are the only copy |
| `src/load.py` | all files in `data/raw/` | M2 tables in `data/flights.db` (`aircraft`, `aircraft_state`, `location`, `weather_hourly`, `quarantine`) | raw files |
| `src/clean.py` | M2 tables | M3 tables (`*_clean`, flags) | M2 tables (full rebuild) |
| `src/validate.py` | M3 tables | PASS/WARN/STOP lines, exit 1 on STOP | the database |
| `src/run_pipeline.py` | — | `data/pipeline_log.db`, table `pipeline_run` | not needed for anything else |
| `src/monitor.py` | run log + database (read-only) | PASS/ALARM lines, exit 1 on ALARM | — |

## 2. Design decisions

| Decision | Our choice | Why |
|---|---|---|
| Ingestion: where each run starts | Open-Meteo: `past_days = ceil((hours since the last landed file + overlap) / 24)`, at most 92. OpenSky: nothing to compute (live snapshot only) | A scheduled run that asks for "the last hour" loses an hour for every missed run; the machine is off most of the day. The last landed file name is the record of the last successful retrieval |
| Overlap | 30 hours (`INGEST_OVERLAP_HOURS`) | Delay table below: Open-Meteo revised hours up to 28.7 h after the hour they describe |
| Loading | full reload of all raw files every run | Simple and correct. It took 0.2 s for 32 files (about 6 ms per file) on the development copy; with at most 48 new files a day that is ~2 minutes after a year, and the step timeout is 15 minutes. Re-measure on the schedule machine **(fill in: `python src/load.py` time)** |
| Version rule | `retrieved_at` (from the raw file name); upsert guarded with `WHERE excluded.retrieved_at > <table>.retrieved_at` | Open-Meteo does revise past hours; the source gives no version stamp of its own. Proven by drill 6 |
| Cleaning | full rebuild of the cleaned layer every run | Flags are computed on the whole history, so batch boundaries cannot change them. Consequence: an old flag can change when history grows (stuck-value runs and neighbour rules see more context) |
| The clock | `--run-time` (default: now), passed to every step; `datetime.now()` only for log timestamps and for the clock's own default. Two deliberate exceptions: ingest names a raw file after the real retrieval time (that *is* the record), and its console log timestamps | One clock makes a backfill (`--run-time` in the past) behave like the original run |
| Overlapping runs | lock file `data/pipeline.lock` (`O_CREAT \| O_EXCL`), removed in `finally`; a lock older than 1.5 h is treated as stale | The machine is switched off every day; a crash or shutdown must not block the next run for ever. Age is used instead of a process id because `os.kill(pid, 0)` is unsafe on Windows |

### How late our data arrives

Query: `python ops/delay_table.py` (reads `data/raw/` only). Output on the raw files of 2026-10-09 .. 2026-11-03 **(re-run and paste yours)**:

```
OpenSky: 3030 states; age at retrieval in minutes: median -0.1, p95 4.4, p99 14.8, max 60.4
Open-Meteo: newest observed hour is behind the retrieval by (h): median 0.6, p95 0.9, max 0.9
Open-Meteo: 960 hours appear in more than one file; 250 changed value (largest change 7.5 km/h)
Open-Meteo: revised hours were up to 28.7 h old when last re-fetched (median 18.7 h)
```

Reading it: OpenSky states arrive almost immediately (p99 15 min, max 60 min), so OpenSky needs no overlap. Open-Meteo's newest hour is under an hour old, but about a quarter of the hours seen twice changed value, and the oldest revision we saw was 28.7 h old. Our files only look back about 24 h (`past_days = 1`), so we cannot see revisions older than that: 30 h is a measured minimum, not a proven upper bound. With the 30 h overlap an hourly run asks for `past_days = 2`.

### The source's memory

- **OpenSky `/states/all`: none.** It returns only the current snapshot. Every hour in which the pipeline did not run is lost for good, and nothing can recover it. `ingest.py` prints a `WARN` naming the gap (more than 2 h since the last snapshot) and `monitor.py` raises an ALARM for gaps over 36 h. This is a limit of the source, not a bug.
- **Open-Meteo: 92 days** (`past_days` maximum). An outage up to 92 days is healed in one run. Beyond that `ingest.py` fetches 92 days, prints a `WARN` naming the lost period, and the completeness check in `monitor.py` shows the hole. Within 92 days, one run after switching the machine on recovers all missed weather.

## 3. Operations

- **Run by hand:** `python src/run_pipeline.py` (add `--run-time 2026-11-20T08:10Z` to choose the clock).
- **Rebuild / backfill:** `python src/run_pipeline.py --skip-ingest` rebuilds the whole database from landed raw data without the network. Because load and clean are full rebuilds, re-running it with the same `--run-time` gives identical tables (checked by hashing every table twice). After fixing a cleaning rule, this one command replaces the cleaned layer for the whole period; nothing is duplicated.
- **Schedule (Windows):** `powershell -ExecutionPolicy Bypass -File ops\install_task.ps1` registers the hourly task `FlightWeatherPipeline`; `ops\install_monitor_task.ps1` registers `FlightWeatherMonitor`. Both use "run as soon as possible after a missed start". Export: `schtasks /query /tn FlightWeatherPipeline /xml > ops\task_scheduler.xml` (and `...Monitor... > ops\task_scheduler_monitor.xml`).
- **Exit codes:** 0 success · 1 a step failed (also the code a failing step returns) · 2 ingest: authentication failed / `validate.py`: bad argument · 75 another run in progress (nothing ran) · 124 a step exceeded its timeout · 127 a step could not be started. `monitor.py`: 0 all PASS, 1 at least one ALARM.
- **Timeouts:** ingest 900 s, load 900 s, clean 900 s, validate 600 s (`STEPS` in `src/run_pipeline.py`).
- **Logs:** `logs/pipeline.log` and `logs/monitor.log` (Task Scheduler output, git-ignored); `data/pipeline_log.db`, table `pipeline_run` (git-ignored, submitted separately).
- **Thresholds:** constants at the top of `src/monitor.py`, each with its reason.
- **Drill hooks (off by default):** `PIPELINE_DRILL_HANG=src/clean.py` makes that step sleep; `PIPELINE_TIMEOUT_OVERRIDE=5` gives every step 5 s. Never left on in a tagged commit.

### validate.py warnings

`stuck_value` and `altitude_mismatch` flags were a WARN on every run (the flagged rows stay in the history). They are now share checks: WARN only if more than 1 % (stuck) or 2 % (altitude) of rows carry the flag; today 0.1 % and 0.6 %. Reason: a WARN that always fires is never read. Freshness is measured from `--run-time`; the volume check WARNs when a run retrieved zero aircraft states or more than 10x the typical retrieval (a `--skip-ingest` rebuild fetches nothing, so it shows that WARN by design).

## 4. Evidence

### The schedule

Machine: **(fill in: whose machine, Windows version)**. Interval: hourly at minute 07, monitor at minute 12 (the machine is only on part of the day). Committed as code: `ops/task_scheduler.xml`, `ops/task_scheduler_monitor.xml`, `ops/install_task.ps1`. The machine is switched off between sessions: Open-Meteo data heals, OpenSky data does not.

### The run log

Run `python -c "import sqlite3;c=sqlite3.connect('data/pipeline_log.db');print(c.execute('''SELECT MIN(run_time),MAX(run_time),COUNT(DISTINCT run_id),COUNT(DISTINCT substr(run_time,1,10)) FROM pipeline_run''').fetchall());print(c.execute('''SELECT step,exit_code,COUNT(*) FROM pipeline_run GROUP BY step,exit_code''').fetchall())"` **(fill in: paste output, and the cause of every failure)**.

### Missed runs

What to do: switch the computer off (or disable the task) for a whole day, switch it on again, let the next run finish. **(fill in)**

1. Before: `python src/monitor.py --now <time after the gap>` before the first run after the gap, then the completeness numbers.
2. The gap check ALARMs ("gap(s) over 36 h") and the pipeline run after it fetches `past_days` covering the whole gap (see `logs/pipeline.log`: `openmeteo: asking for past_days=N`).
3. After: `python src/monitor.py` — completeness PASS for weather. Flights stay missing for the gap; that is the source's limit and is stated in section 2.

### Drills

Record for each: what you did, what `pipeline_run` shows, what the runner and `monitor.py` said. Commands for Windows PowerShell, from the repository root. **(fill in the output of each)**

1. **Credential failure.** `$env:OPENSKY_CLIENT_SECRET="wrong"; python src/run_pipeline.py; echo $LASTEXITCODE` → `ingest.py` exits 2, the runner exits 2, no later step ran, one row for `src/ingest.py`. Then `Remove-Item Env:OPENSKY_CLIENT_SECRET`.
2. **A step that fails.** Put a broken file in the raw folder: `echo "{broken" > data\raw\opensky\20990101T000000Z.json`, run `python src/run_pipeline.py --skip-ingest`, expect exit 1 at `src/load.py`, `nothing was written to the database`, and an unchanged database file. Then delete the broken file.
3. **A step that hangs.** `$env:PIPELINE_DRILL_HANG="src/clean.py"; $env:PIPELINE_TIMEOUT_OVERRIDE="5"; python src/run_pipeline.py --skip-ingest` → exit 124, logged. Then remove both variables.
4. **Two runs at once.** Window 1: the hang drill with `PIPELINE_TIMEOUT_OVERRIDE="60"`. Window 2, within the minute: `python src/run_pipeline.py --skip-ingest; echo $LASTEXITCODE` → 75, `Nothing was run`, no log row.
5. **Missed runs.** See the section above.
6. **An old file re-loaded.** `python ops/drill_old_file.py` → loads the newest file with a revised hour, then the oldest file with the older value into the same scratch database; `DRILL PASSED: the newer value stayed`. (Passed on the development copy: older value 8.2, newer 7.7, 7.7 stayed.)

Each `monitor.py` check must also be shown to ALARM once. The thresholds can be overridden by environment variable for that only, e.g. `$env:MAX_GAP_HOURS="1"; python src/monitor.py`. For last success: `python src/monitor.py --now <3 days later>`. For completeness: delete a few `weather_hourly` rows on a **copy** of the database. For freshness: `--now` far in the future. **(fill in)**

### monitor.py on the day we tagged

**(fill in: paste `python src/monitor.py`)**

## 5. What can still go wrong

- **Flights lost while the machine is off.** The largest hole in the data; no code can recover it.
- **The machine never starts the task** (sleep, "only when logged on", a Windows update): the monitor runs on the same machine, so if the whole machine is off nothing is reported until it is switched on again. A second machine or a cloud check would be needed.
- **A revision older than the overlap.** The 30 h overlap is a measured minimum; Open-Meteo could revise a value later than that.
- **A silently changed API format.** A new field or an empty list may be accepted if it parses; only some changes fail the run.
- **Stale lock after a crash within 1.5 h** blocks the next run until the lock is older than that (the monitor will ALARM if it lasts a day).
- **Run log and database on the same disk.** A disk failure loses both; nothing is copied elsewhere.
- **The volume check is crude** (median of earlier retrievals); a slow decline in the number of aircraft would not be noticed.
- **Thresholds are for a daily-on machine** (36 h). A machine that is on continuously would need much smaller ones.
