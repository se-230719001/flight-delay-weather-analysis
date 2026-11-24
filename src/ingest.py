"""M1 ingestion script: lands raw API responses, untouched, in data/raw/<source>/.

Run from the repository root:
    python src/ingest.py

Sources:
    - OpenSky Network (ADS-B state vectors)  -> data/raw/opensky/<UTC timestamp>.json
    - Open-Meteo (hourly weather)            -> data/raw/openmeteo/<UTC timestamp>.json

Responses are saved byte-for-byte as received (no parsing, cleaning or renaming).
Files are never overwritten: every run creates new timestamped files.
All configuration and credentials come from environment variables (.env supported).

M5 - the run heals itself after missed runs (python src/ingest.py [--run-time 2026-11-20T08:10Z]):
    - Open-Meteo: asks for everything since the last landed file, plus an overlap
      (past_days = ceil((hours since last retrieval + OVERLAP_HOURS) / 24), at most 92 days,
      which is all the API can give). A machine that was off for a day catches up in one run.
    - OpenSky: /states/all is a live snapshot with no history, so a missed period can NOT be
      recovered. The run says so loudly (WARN naming the gap); src/monitor.py raises the ALARM.
Exit codes: 0 all sources fetched, 1 a source failed, 2 authentication failed.
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from db import (add_run_time_argument, format_run_time, resolve_run_time,
                retrieved_at_from_filename)

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"

# --- Request behaviour -------------------------------------------------------
CONNECT_TIMEOUT = 10      # seconds
READ_TIMEOUT = 30         # seconds
MAX_ATTEMPTS = 5          # bounded number of attempts per request
BACKOFF_BASE = 2          # delays: 2, 4, 8, 16 ... seconds
MAX_DELAY = 120           # never wait longer than this between attempts

# --- Configuration (all from environment, with non-secret defaults) ----------
OPENSKY_URL = os.environ.get("OPENSKY_URL", "https://opensky-network.org/api/states/all")
OPENSKY_TOKEN_URL = os.environ.get(
    "OPENSKY_TOKEN_URL",
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token",
)
# lamin,lomin,lamax,lomax  (default: bounding box around Turkey)
OPENSKY_BBOX = os.environ.get("OPENSKY_BBOX", "36.0,26.0,42.0,45.0")

OPENMETEO_URL = os.environ.get("OPENMETEO_URL", "https://api.open-meteo.com/v1/forecast")
# "lat,lon;lat,lon;..."  (default: IST, SAW, ESB, ADB, AYT airports)
OPENMETEO_LOCATIONS = os.environ.get(
    "OPENMETEO_LOCATIONS",
    "41.2753,28.7519;40.8986,29.3092;40.1281,32.9951;38.2924,27.1570;36.8987,30.8005",
)
OPENMETEO_HOURLY = os.environ.get(
    "OPENMETEO_HOURLY",
    "wind_speed_10m,wind_gusts_10m,precipitation,showers,cape",
)
OPENMETEO_PAST_DAYS = os.environ.get("OPENMETEO_PAST_DAYS", "1")   # minimum look-back
OPENMETEO_FORECAST_DAYS = os.environ.get("OPENMETEO_FORECAST_DAYS", "1")

# --- Self-healing (M5) -----------------------------------------------------------
# Overlap re-fetched on top of the gap since the last retrieval, so late or revised values
# are picked up again. Evidence (docs/pipeline.md, "How late our data arrives"): Open-Meteo
# revised 250 of 960 hours that appeared in more than one file, up to 28.7 h after the hour
# they describe. 30 h covers that; OpenSky states are at most ~1 h old, so it needs no overlap.
OVERLAP_HOURS = float(os.environ.get("INGEST_OVERLAP_HOURS", "30"))
MAX_PAST_DAYS = 92   # Open-Meteo's own limit for past_days: an outage longer than this loses data
# OpenSky keeps no history: any gap longer than this is data that is gone for good.
OPENSKY_MAX_GAP_HOURS = float(os.environ.get("OPENSKY_MAX_GAP_HOURS", "2"))


class AuthError(Exception):
    """401/403: stop the whole run immediately, never retry."""


class FetchError(Exception):
    """A source could not be fetched (after bounded retries) or was invalid."""


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def last_retrieval(source):
    """When did we last land a file for this source? None if never. The raw file names are
    the record: a landed file means a successful retrieval."""
    stamps = []
    for path in (RAW_DIR / source).glob("*.json"):
        try:
            stamps.append(retrieved_at_from_filename(path))
        except ValueError:
            continue   # not one of ours
    if not stamps:
        return None
    return datetime.strptime(max(stamps), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def hours_since(last, run_time):
    return 0.0 if last is None else max(0.0, (run_time - last).total_seconds() / 3600)


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%SZ')}] {msg}", flush=True)


def retry_delay(resp, attempt):
    """Delay before the next attempt: server hint if given, else exponential backoff."""
    if resp is not None:
        for header in ("Retry-After", "X-Rate-Limit-Retry-After-Seconds"):
            value = resp.headers.get(header)
            if value and value.isdigit():
                return min(int(value), MAX_DELAY)
    return min(BACKOFF_BASE ** attempt, MAX_DELAY)


def request_with_retry(session, method, url, **kwargs):
    """HTTP request with timeout, bounded retries and the required error behaviour."""
    last_reason = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        resp = None
        try:
            resp = session.request(
                method, url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), **kwargs
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_reason = f"network problem: {type(exc).__name__}"
        else:
            code = resp.status_code
            if code in (401, 403):
                raise AuthError(
                    f"Authentication failed (HTTP {code}) for {url}. "
                    "Check the credentials in your .env file. Not retrying."
                )
            if code == 429 or code >= 500:
                last_reason = f"HTTP {code}"
            elif code >= 400:
                raise FetchError(f"HTTP {code} for {url} (client error, not retrying)")
            else:
                return resp

        if attempt == MAX_ATTEMPTS:
            break
        delay = retry_delay(resp, attempt)
        log(f"  attempt {attempt}/{MAX_ATTEMPTS} failed ({last_reason}); retrying in {delay}s")
        time.sleep(delay)

    raise FetchError(f"Giving up after {MAX_ATTEMPTS} attempts: {last_reason} ({url})")


def save_raw(source, content):
    """Save bytes exactly as received. Never overwrites an existing file."""
    folder = RAW_DIR / source
    folder.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    counter = 0
    while True:
        suffix = f"_{counter}" if counter else ""
        path = folder / f"{stamp}{suffix}.json"
        try:
            with open(path, "xb") as fh:  # "x" = fail if the file already exists
                fh.write(content)
            return path
        except FileExistsError:
            counter += 1


def parse_json_or_fail(resp, source):
    """Only used to validate the response; the saved file is the original bytes."""
    try:
        return json.loads(resp.content)
    except ValueError:
        raise FetchError(f"{source}: response is not valid JSON")


# --- OpenSky -----------------------------------------------------------------
def opensky_headers_and_auth(session):
    """OAuth2 client credentials if set, else basic auth if set, else anonymous."""
    client_id = os.environ.get("OPENSKY_CLIENT_ID")
    client_secret = os.environ.get("OPENSKY_CLIENT_SECRET")
    username = os.environ.get("OPENSKY_USERNAME")
    password = os.environ.get("OPENSKY_PASSWORD")

    if client_id and client_secret:
        resp = request_with_retry(
            session,
            "POST",
            OPENSKY_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        token = resp.json().get("access_token")
        if not token:
            raise AuthError("OpenSky token response contained no access_token.")
        return {"Authorization": f"Bearer {token}"}, None
    if username and password:
        return {}, (username, password)
    log("  no OpenSky credentials set: using anonymous access (strict rate limits)")
    return {}, None


def ingest_opensky(session, run_time):
    last = last_retrieval("opensky")
    gap = hours_since(last, run_time)
    if last is not None and gap > OPENSKY_MAX_GAP_HOURS:
        log(f"  WARN opensky: no snapshot for {gap:.1f} h (last {last:%Y-%m-%dT%H:%MZ}). "
            "OpenSky has no history: this period is lost for good.")
    try:
        lamin, lomin, lamax, lomax = [float(x) for x in OPENSKY_BBOX.split(",")]
    except ValueError:
        raise FetchError("OPENSKY_BBOX must be 'lamin,lomin,lamax,lomax'")
    headers, auth = opensky_headers_and_auth(session)
    params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}

    # /states/all returns the complete result in a single response (no pagination).
    resp = request_with_retry(session, "GET", OPENSKY_URL, params=params, headers=headers, auth=auth)
    data = parse_json_or_fail(resp, "opensky")
    states = data.get("states") if isinstance(data, dict) else None
    if not states:
        raise FetchError("opensky: HTTP 200 but no aircraft states in the response (empty)")
    path = save_raw("opensky", resp.content)
    return len(states), path


# --- Open-Meteo --------------------------------------------------------------
def meteo_past_days(run_time):
    """How many whole days back to ask for: the gap since the last landed file plus the overlap."""
    minimum = int(OPENMETEO_PAST_DAYS)
    last = last_retrieval("openmeteo")
    if last is None:
        return minimum
    needed = math.ceil((hours_since(last, run_time) + OVERLAP_HOURS) / 24)
    if needed > MAX_PAST_DAYS:
        log(f"  WARN openmeteo: last retrieval {last:%Y-%m-%dT%H:%MZ} is {needed} days back; "
            f"the API only returns {MAX_PAST_DAYS}. Older hours are lost.")
        return MAX_PAST_DAYS
    return max(minimum, needed)


def ingest_openmeteo(session, run_time):
    past_days = meteo_past_days(run_time)
    log(f"  openmeteo: asking for past_days={past_days}")
    try:
        coords = [tuple(float(v) for v in loc.split(",")) for loc in OPENMETEO_LOCATIONS.split(";")]
        if any(len(c) != 2 for c in coords):
            raise ValueError
    except ValueError:
        raise FetchError("OPENMETEO_LOCATIONS must be 'lat,lon;lat,lon;...'")
    params = {
        "latitude": ",".join(str(c[0]) for c in coords),
        "longitude": ",".join(str(c[1]) for c in coords),
        "hourly": OPENMETEO_HOURLY,
        "past_days": past_days,
        "forecast_days": OPENMETEO_FORECAST_DAYS,
        "timezone": "UTC",
    }

    # Multiple locations are returned together in one response (no pagination).
    resp = request_with_retry(session, "GET", OPENMETEO_URL, params=params)
    data = parse_json_or_fail(resp, "openmeteo")
    locations = data if isinstance(data, list) else [data]
    valid = [loc for loc in locations if isinstance(loc, dict) and loc.get("hourly", {}).get("time")]
    if not valid:
        raise FetchError("openmeteo: HTTP 200 but no hourly data in the response (empty)")
    path = save_raw("openmeteo", resp.content)
    return len(valid), path


SOURCES = [
    ("opensky", "aircraft states", ingest_opensky),
    ("openmeteo", "locations", ingest_openmeteo),
]


def main():
    parser = argparse.ArgumentParser(description="Land raw API responses in data/raw/.")
    add_run_time_argument(parser)
    run_time = resolve_run_time(parser.parse_args().run_time)
    log(f"Ingestion run started (run time {format_run_time(run_time)})")
    results = []  # (source, status, detail)
    aborted = False

    with requests.Session() as session:
        for name, unit, func in SOURCES:
            log(f"Fetching {name} ...")
            try:
                count, path = func(session, run_time)
                results.append((name, "OK", f"{count} {unit} -> {path.relative_to(ROOT)}"))
            except AuthError as exc:
                results.append((name, "AUTH FAILED", str(exc)))
                aborted = True
                break  # stop immediately, do not try remaining sources
            except FetchError as exc:
                results.append((name, "FAILED", str(exc)))
            except Exception as exc:  # unexpected: report, don't crash silently
                results.append((name, "ERROR", f"{type(exc).__name__}: {exc}"))

    print("\n===== RUN SUMMARY =====")
    for name, status, detail in results:
        print(f"{name:<10} {status:<12} {detail}")
    skipped = [n for n, _, _ in SOURCES if n not in {r[0] for r in results}]
    for name in skipped:
        print(f"{name:<10} {'SKIPPED':<12} run aborted after authentication failure")
    ok = sum(1 for r in results if r[1] == "OK")
    print(f"{ok}/{len(SOURCES)} sources fetched successfully.")
    print("Run is HEALTHY." if ok == len(SOURCES) else "Run is NOT healthy: see failures above.")

    if aborted:
        sys.exit(2)
    sys.exit(0 if ok == len(SOURCES) else 1)


if __name__ == "__main__":
    main()