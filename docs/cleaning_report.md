# Cleaning Report: flight-delay-weather-analysis

> DRAFT. Counts are from the database built on 2026-10-20 (4 OpenSky snapshots, 4 Open-Meteo files). Re-run `load.py`, `clean.py` and `validate.py` after fetching more data and update every number. "Investigated by" is a proposed split: change it to who really did the work.

## 1. Summary
TODO (5-6 sentences at the end: what data, the most important problems, what the cleaned layer contains, what we still can't say, and any M2 issues fixed since the M2 tag).

## 2. Profile highlights
Profile: [notebooks/m3_profile.ipynb](../notebooks/m3_profile.ipynb). TODO: paste the 2-3 tables that led to findings (missing share by `on_ground`; gust robust-z counts per location; `|geo - baro|` altitude gap).

## 3. Issues register

| ID | Family | Table.column | What | Where / when / how many | Evidence | Cause | Decision | Flag | Investigated by |
|---|---|---|---|---|---|---|---|---|---|
| I-01 | 1 Missing | aircraft_state.baro_altitude, geo_altitude, vertical_rate | NULL almost only when the aircraft is on the ground | 42 ground states: baro NULL in 40, geo NULL in 42, vertical rate NULL in 38; airborne states (748): baro NULL in 1 | Cross-tab of NULL by `on_ground` (notebook section 2) | Inferred: ground transmissions often carry no altitude; no document read yet | NULL, kept as missing, reason recorded | `missing_on_ground` | Enes Akdeniz |
| I-02 | 1 Missing | aircraft_state.baro_altitude, geo_altitude | NULL while airborne | baro 1 row, geo 5 rows (of 748 airborne) | Same cross-tab | Unknown | NULL, kept, flagged | `missing_not_reported` | Enes Akdeniz |
| I-03 | 1 Missing | aircraft_state.squawk | NULL in 218 of 790 states | 45% of ground states (19 of 42), 27% of airborne (199 of 748) | Share by `on_ground` differs, so not MCAR | Unknown (transponder not squawking or message not received) | NULL, kept, flagged; squawk is not used in the analysis | `missing_not_reported` | Nihat Avcı |
| I-04 | 1 Missing / 5 Text | aircraft_state.callsign | Empty string instead of NULL | 20 states (13 airborne, 7 on ground) | `repr()` shows `''` or spaces only | Inferred: the aircraft sent no callsign | Cleaned value NULL | `missing_empty` | Nihat Avcı |
| I-05 | 5 Text | aircraft_state.callsign | Fixed-width values padded with trailing spaces | 768 of 790 states | `repr()` shows e.g. a callsign followed by spaces | Inferred: fixed 8-character field | Trim whitespace only (no case change, no renaming) | `trimmed` | Nihat Avcı |
| I-06 | 4 Across fields | aircraft_state.baro_altitude vs geo_altitude | The two altitudes contradict each other by more than 1,000 m | 2 states, both in snapshot 2026-10-20T10:14:56Z: `73828a` (1,333.5 vs 10,591.8 m), `152073` (10,248.9 vs 11,491.0 m) | Median gap 274 m, 95th percentile 434 m (notebook section 8) | Unknown: we cannot tell which altitude is wrong | Both set to NULL, raw values kept | `altitude_mismatch` | Mehmetali Açık |
| I-07 | 3 Unusual | weather_hourly.wind_gusts_10m | Extreme for that location, but possible | 28 hours (AYT 9, ESB 14, SAW 5) with robust z > 3.5 per location | IQR fence and robust z compared (notebook section 9); IST's largest gusts (41.4 km/h, 9 Oct 09:00-11:00 UTC) are not flagged because IST's spread is wide | Inferred: real wind events; gusts never fall below the mean wind speed | Kept unchanged, flagged | `unusual_high` | Mehmetali Açık |
| I-08 | 3 Unusual | weather_hourly.cape | High CAPE at Antalya | 6 hours, AYT, 8 Oct 12:00-17:00 UTC, peak 1,650 J/kg | CAPE is 0 most hours, so IQR and MAD are 0 and unusable; a domain threshold (1,000 J/kg) is used instead | Inferred: real convective instability (the hours are consecutive and smooth) | Kept, flagged | `unusual_high` | Mehmetali Açık |
| I-09 | 4 Across rows | aircraft_state.time_position vs retrieved_at | `time_position` is later than the file's `retrieved_at` | 472 of 790 states, by at most 3 s | Notebook section 8 (age at retrieval) | Inferred: file name stamp has 1 s resolution and is taken on our clock; OpenSky time comes from its server | No change, no flag; do not treat `retrieved_at` as an exact time | none | Enes Akdeniz |
| I-10 | 6 Identity | aircraft_state.callsign per aircraft | One aircraft carries different callsigns over time | 20 of 461 aircraft, e.g. `151e1b`: PBD840 then PBD798 | Notebook section 10 | Inferred: one aircraft flies different flights | No merge: `icao24` stays the identity; callsign stays per state | none | Nihat Avcı |
| I-11 | 6 Identity | aircraft_state.callsign | One callsign used by two aircraft | `PALAZ03`: 2 `icao24` values | Notebook section 10 | Unknown | No merge, nothing changed | none | Nihat Avcı |
| I-12 | 6 Identity / 5 Categories | location | Weather locations have no ID or name (the first has no `location_id`); names live only in `.env` order | 5 locations | Returned grid points are 2.5-5 km from the requested airport coordinates | Confirmed by our own request list (`ingest.py` defaults, order IST, SAW, ESB, ADB, AYT) | Mapping table `location_airport` loaded from `data/mappings/location_airport.csv` | none | Mehmetali Açık |
| I-13 | 1 Missing (whole periods) | all tables | Time coverage has large gaps | Weather only for 8, 9, 19, 20 Oct (24 h x 5 locations each); aircraft only in 4 snapshots | `GROUP BY` day | Confirmed: ingestion was run by hand on those days | Nothing to clean; M4 cannot compare across the gap or treat snapshots as continuous | none | Enes Akdeniz |
| I-14 | 5 Units | aircraft_state vs weather_hourly | Different units across sources: altitude m, speed m/s; wind km/h | all numeric columns | `hourly_units` in the Open-Meteo files; OpenSky field description | Confirmed (source documentation) | Native units kept in M3; any conversion happens in M4 and says so | none | Nihat Avcı |
| I-15 | 4 Across rows | weather_hourly (all columns) | Hours after the retrieval time are forecasts, and a later run overwrites them | Every file contains future hours (`forecast_days=1`) | `docs/sources.md`; same-day refetches were identical | Confirmed (Open-Meteo is a forecast API) | Not flagged (the loader cannot tell forecast from observation); M4 must only use hours before `retrieved_at` | none | Mehmetali Açık |

### Checked and not found

| Family | Check | Result |
|---|---|---|
| 2 Invalid | Sentinels -999, -9999, 9999, 99999 in every numeric column of both sources | 0 rows (notebook section 3) |
| 2 Invalid | Latitude/longitude outside the requested bounding box; negative velocity; `true_track` outside 0-360; negative baro altitude | 0 each (notebook section 7) |
| 2 Invalid | Weather: negative values; gust below mean wind speed; showers above precipitation | 0 each |
| 2 Invalid | `squawk` not four octal digits; emergency codes 7500/7600/7700 | 0 each |
| 1 Missing | NULL values in `weather_hourly` | 0 in all five variables |
| 4 Across rows | Stuck wind values: longest run of identical hourly values | 2 h (wind speed), 4 h (gusts), below `STUCK_RUN = 6` |
| 4 Across rows | Spikes: largest hour-to-hour wind speed change | 10.3 km/h, no spike |
| 4 Across fields | `last_contact` before `time_position` | 0 rows |
| 5 Text | `origin_country`: values not NFC-normalised, non-ASCII characters, case/whitespace duplicates | none of the three |
| 5 Text | `icao24` is 6 lowercase hex characters | all 470 aircraft |
| 6 Identity | One `icao24` with more than one `origin_country` | 0 (M2 key test) |
| (M2) | Duplicates by key, quarantined records | 0 after load (duplicates are M2's job) |

## 4. Missing values
TODO per pattern: where and when, mechanism, consequence for M4. Starting points:
- **Altitude and vertical rate (I-01/I-02):** MAR. Missingness depends on `on_ground`, which we observe (95% of ground states have no baro altitude, 0.1% of airborne). Consequence: averages of altitude over all states silently mean "airborne only"; ground states must be handled explicitly.
- **Squawk (I-03):** not MCAR (45% on the ground vs 27% airborne); the remaining cause is unknown. Consequence: do not use `squawk` as a feature without checking this.
- **Whole periods (I-13):** MNAR in effect, because the data exists only on the days we ran the ingestion. Consequence: no trends or delay comparisons across 10-18 Oct.

## 5. Flags and rules
Rule order (first rule that applies wins): 1 missing, 2 invalid, 3 stuck, 4 unusual (flagged and kept), 5 ok. Full list of allowed values: `sql/clean_schema.sql`.

| Order | Flag | Meaning | Rule | Cleaned value | Count (20 Oct build) |
|---|---|---|---|---|---|
| 1 | `missing_on_ground` | NULL while `on_ground = 1` | raw IS NULL and on_ground = 1 | NULL | baro 40, geo 42, vertical rate 38 |
| 1 | `missing_not_reported` | NULL otherwise | raw IS NULL | NULL | baro 1, geo 5, squawk 218 |
| 1 | `missing_empty` | callsign is an empty string | TRIM(raw) = '' | NULL | 20 |
| 2 | `invalid_range` | outside the plausible range | see constants | NULL | 0 |
| 2 | `altitude_mismatch` | baro and geo disagree | abs(geo - baro) > `ALT_GAP_MAX_M` | NULL (both) | 2 states |
| 2 | `invalid_gust_below_speed`, `invalid_showers_above_precip`, `invalid_format` | contradicts another field / format | see `clean.py` | NULL | 0 |
| 3 | `stuck_value` | identical non-zero wind value for `STUCK_RUN` consecutive hours | run detection per location | NULL | 0 |
| 4 | `unusual_high` | extreme but possible | gust robust z > `ROBUST_Z_THRESHOLD` per location; CAPE > `CAPE_UNUSUAL_JKG` | unchanged | gusts 28, CAPE 6 |
| 4 | `emergency_code` | squawk 7500/7600/7700 | in `EMERGENCY_SQUAWKS` | unchanged | 0 |
| 4 | `trimmed` | callsign had padding | TRIM(raw) <> raw | trimmed text | 768 |
| 5 | `ok` | nothing to change | | raw value | the rest |

### Constants
TODO: one sentence for each constant in `src/clean_config.py` (why this value, what changes if you move it). The comments in that file hold the profile evidence to copy from: `ALT_MIN_M`, `ALT_MAX_M`, `ALT_GAP_MAX_M`, `VELOCITY_MAX_MS`, `VERTICAL_RATE_MAX_MS`, `BBOX`, `WIND_MAX_KMH`, `PRECIP_MAX_MM_H`, `CAPE_MAX_JKG`, `STUCK_RUN`, `ROBUST_Z_THRESHOLD`, `CAPE_UNUSUAL_JKG`, `UNUSABLE_SHARE_MAX`, `AIRBORNE_ALT_USABLE_MIN`, `FRESHNESS_HOURS`. A constant you cannot justify is a guess: say so.

## 6. Mapping and identity decisions
- `data/mappings/location_airport.csv`: 5 of 5 locations matched by `location_index` (position in the request). Evidence: grid point 2.5-5 km from the requested airport coordinates (see the `note` column). TODO: fill `decided_by` with the real person.
- No identity merges were needed (I-10, I-11): one aircraft changing callsign is normal and `icao24` is stable; a shared callsign is not evidence of one aircraft.

## 7. Counts and validation
TODO: paste the final output of `python src/clean.py` (run twice) and `python src/validate.py`. Classify every WARN as a problem in the world, the measurement or the pipeline (the current WARN, `altitude_mismatch` flags: measurement).
How each STOP check was made to fail once: `python src/validate.py --selftest` (temporary copy: deleted one cleaned weather row; set one baro altitude to -999; made one cleaned wind speed differ from its raw value; pointed one cleaned state at a non-existent aircraft).

## 8. What we can't say
TODO. Starting points: forecast vs observed weather is not separated (I-15); only 4 days of weather and 4 snapshots (I-13); the cause of altitude contradictions and missing squawks is unknown; the 0.25 degree box used to link aircraft to airports is an approximation.