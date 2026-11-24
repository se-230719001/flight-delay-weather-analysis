"""How late does our data arrive, and is it revised? (evidence for the ingestion overlap)

    python ops/delay_table.py

Reads data/raw/ only. OpenSky: age of each state (retrieval time minus time_position).
Open-Meteo: how far behind the retrieval the newest observed hour is, and how many hours
changed value between files, and how old those hours were when last re-fetched.
"""
import collections
import glob
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


def stamp(path):
    m = re.search(r"(\d{8}T\d{6}Z)", os.path.basename(path))
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def pct(values, q):
    return values[min(len(values) - 1, int(len(values) * q))]


def main():
    ages = []
    for p in sorted(glob.glob(str(RAW / "opensky" / "*.json"))):
        t = stamp(p)
        for s in json.load(open(p, encoding="utf-8")).get("states") or []:
            if s[3] is not None:
                ages.append((t.timestamp() - s[3]) / 60)
    ages.sort()
    print("OpenSky: %d states; age at retrieval in minutes: median %.1f, p95 %.1f, p99 %.1f, max %.1f"
          % (len(ages), pct(ages, .5), pct(ages, .95), pct(ages, .99), ages[-1]))

    lag, versions = [], collections.defaultdict(list)
    for p in sorted(glob.glob(str(RAW / "openmeteo" / "*.json"))):
        t = stamp(p)
        data = json.load(open(p, encoding="utf-8"))
        for i, loc in enumerate(data if isinstance(data, list) else [data]):
            newest = None
            for x, v in zip(loc["hourly"]["time"], loc["hourly"]["wind_speed_10m"]):
                ts = datetime.strptime(x, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
                versions[(i, x)].append((t, v))
                if v is not None and ts <= t:
                    newest = ts
            if newest:
                lag.append((t - newest).total_seconds() / 3600)
    lag.sort()
    print("Open-Meteo: newest observed hour is behind the retrieval by (h): median %.1f, p95 %.1f, max %.1f"
          % (pct(lag, .5), pct(lag, .95), lag[-1]))
    multi = revised = 0
    biggest, ages_h = 0.0, []
    for (i, x), seen in versions.items():
        if len(seen) < 2:
            continue
        multi += 1
        values = {v for _, v in seen if v is not None}
        if len(values) > 1:
            revised += 1
            biggest = max(biggest, max(values) - min(values))
            ts = datetime.strptime(x, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
            ages_h.append(max(0.0, (seen[-1][0] - ts).total_seconds() / 3600))
    ages_h.sort()
    print("Open-Meteo: %d hours appear in more than one file; %d changed value (largest change %.1f km/h)"
          % (multi, revised, biggest))
    if ages_h:
        print("Open-Meteo: revised hours were up to %.1f h old when last re-fetched (median %.1f h)"
              % (ages_h[-1], pct(ages_h, .5)))


if __name__ == "__main__":
    main()