"""All thresholds and constants of the M3 cleaning step, in one place.

Each constant has a name, a unit and the reason for its value. Every one of them must be
justified in docs/cleaning_report.md (section 'Constants').

Rule order (first rule that applies wins; one flag per value):
  1. missing   - the value is NULL (reason recorded: on the ground / not reported / empty)
  2. invalid   - the value cannot be true (out of range, contradicts another field)
  3. stuck     - the value repeated for STUCK_RUN consecutive hours
  4. unusual   - extreme but possible: FLAGGED AND KEPT
  5. ok
"""

# --- OpenSky: plausible ranges (invalid_range) --------------------------------
BBOX = (36.0, 26.0, 42.0, 45.0)   # lamin, lomin, lamax, lomax: the area we requested (ingest.py)
ALT_MIN_M = -500.0                # below this an altitude is impossible (lowest land is about -430 m)
ALT_MAX_M = 20000.0               # above this no airliner can fly; profile maximum was 13,472 m
ALT_GAP_MAX_M = 1000.0            # |geo - baro| above this: the two altitudes contradict each other;
                                  # profile: median gap 274 m, 95th percentile 434 m
VELOCITY_MAX_MS = 400.0           # m/s (about 1,440 km/h); profile maximum was 288 m/s
VERTICAL_RATE_MAX_MS = 100.0      # m/s; profile range was -20 to +19 m/s

# --- OpenSky: squawk ----------------------------------------------------------
EMERGENCY_SQUAWKS = ("7500", "7600", "7700")   # hijack, radio failure, general emergency (flagged, kept)

# --- Open-Meteo: plausible ranges (invalid_range) -----------------------------
WIND_MAX_KMH = 300.0              # beyond any recorded surface wind at an airport
PRECIP_MAX_MM_H = 300.0           # above the heaviest hourly rainfall ever recorded
CAPE_MAX_JKG = 10000.0            # above the largest CAPE ever observed (about 8,000)

# --- Open-Meteo: stuck values and unusual values ------------------------------
STUCK_RUN = 6                     # consecutive hours of identical non-zero wind values;
                                  # profile: longest run was 2 (wind speed) and 4 (gusts)
ROBUST_Z_THRESHOLD = 3.5          # per-location robust z-score (median and MAD) for gusts;
                                  # 3.5 is the usual cut-off for this score
CAPE_UNUSUAL_JKG = 1000.0         # CAPE is 0 most hours, so IQR and MAD are 0 and cannot be used;
                                  # above 1,000 J/kg is conventionally 'strong instability'

# --- Mapping files ------------------------------------------------------------
MAPPING_FILES = {"location_airport": "location_airport.csv"}   # in data/mappings/

# --- validate.py: thresholds for the WARN checks --------------------------------
UNUSABLE_SHARE_MAX = 0.05         # a weather variable may be NULL in at most 5% of a location's hours;
                                  # profile: 0% missing, so anything above 5% is a real change
AIRBORNE_ALT_USABLE_MIN = 0.95    # at least 95% of airborne states should keep a usable baro altitude;
                                  # profile: 99.9%
FRESHNESS_HOURS = 48              # the newest raw retrieval should be at most this old (we fetch by
                                  # hand until M5 automates it)