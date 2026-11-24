"""How late does our data arrive, and is it revised? (evidence for the ingestion overlap)

    python ops/delay_table.py

Reads data/raw/ only. OpenSky: age of each state (retrieval time minus time_position).
Open-Meteo: how far behind the retrieval the newest observed hour is, and how many hours
changed value between files AFTER they had passed (forecast -> observed is not counted),
and how old the hour was when the change was first seen.
"""
import collections
import glob
import json
import os
import re
from datetime import datetime, timedelta, timezone
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
    # A real revision: the hour had already PASSED at both retrievals (so neither value was a
    # forecast) and the two values differ. A forecast replaced by the observed value is not a
    # revision of the source, so it is not counted.
    hour = timedelta(hours=1)
    refetched = revised = 0
    biggest, ages_h = 0.0, []
    for (i, x), seen in versions.items():
        ts = datetime.strptime(x, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        past = [(t, v) for t, v in sorted(seen) if v is not None and t >= ts + hour]
        if len(past) < 2:
            continue
        refetched += 1
        changed = False
        for (t1, v1), (t2, v2) in zip(past, past[1:]):
            if v1 != v2:
                changed = True
                biggest = max(biggest, abs(v2 - v1))
                ages_h.append((t2 - ts).total_seconds() / 3600)   # age of the hour when the change was first seen
        revised += changed
    ages_h.sort()
    print("Open-Meteo: %d hours were fetched again after they had passed; %d of them changed value "
          "(largest change %.1f km/h)" % (refetched, revised, biggest))
    if ages_h:
        print("Open-Meteo: revisions were first seen when the hour was (h old): median %.1f, p95 %.1f, max %.1f"
              % (pct(ages_h, .5), pct(ages_h, .95), ages_h[-1]))
    else:
        print("Open-Meteo: no revision of an already-passed hour seen yet")


if __name__ == "__main__":
    main()
