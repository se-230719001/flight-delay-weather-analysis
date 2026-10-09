    source_name: OpenSky Network - live ADS-B aircraft state vectors

provider: The OpenSky Network (non-profit research association)
url: https://openskynetwork.github.io/opensky-api/rest.html
access_method: REST API (GET /states/all); anonymous access possible, account credentials recommended (OAuth2 client credentials or username/password, see .env.example)
licence: OpenSky Network terms of use (https://opensky-network.org/about/terms-of-use). Not an open licence. Data is provided for research and non-commercial use only; commercial use requires contacting OpenSky. Publications using the data must cite: Schäfer, Strohmeier, Lenders, Martinovic, Wilhelm, "Bringing Up OpenSky: A Large-scale ADS-B Sensor Network for Research". Raw files are not redistributed in this repository (data/raw is gitignored).
terms_notes: OpenSky data is offered for research and non-commercial use only; commercial use requires contacting OpenSky. Redistribution is not clearly permitted, so data/raw/ is in .gitignore and raw files are not committed to the repository; they are shared only privately among project members (school OneDrive). Attribution to The OpenSky Network (https://opensky-network.org) is given in the README, and publications must cite Schäfer et al., "Bringing Up OpenSky: A Large-scale ADS-B Sensor Network for Research".
update_cadence: live (state vectors refresh every few seconds to ~10 s; each request returns one snapshot)
coverage: Snapshot at retrieval time, restricted to the bounding box set in OPENSKY_BBOX (default: lat 36-42, lon 26-45, i.e. Turkey and surroundings). Historical data requires separate access and is not part of M1.
record_meaning: one row = the latest known state of one aircraft (identified by icao24) at one point in time (position, altitude, velocity, heading, etc.)
join_key: time (UTC) + position (latitude/longitude) -> joins to the Open-Meteo hourly record of the nearest location/hour
first_retrieved: 2026-10-09T14:53:48Z
known_issues:

- Each request is a single snapshot, so a continuous picture needs repeated runs (scheduling is M5).
- Anonymous requests have strict rate limits (HTTP 429 is possible); credentials give higher limits.
- Coverage depends on community-operated receivers; gaps and missing aircraft are possible, especially at low altitude and over water.
- Some fields can be null (e.g. position or altitude when not reported).
- First run failed with `AUTH FAILED (HTTP 401)` for OpenSky because the credentials in `.env` were not in the expected format (JSON pasted instead of plain `OPENSKY_CLIENT_ID=` / `OPENSKY_CLIENT_SECRET=` values). Fixed by putting each value on its own line without quotes or braces. Because the auth error aborts the whole run, Open-Meteo was skipped until this was fixed.
- First successful run (2026-10-09): OpenSky response ~28 KB, Open-Meteo response ~12 KB, one file per source in `data/raw/<source>/`.

source_name: Open-Meteo - hourly weather forecast API
provider: Open-Meteo.com
url: https://open-meteo.com/en/docs
access_method: REST API (GET /v1/forecast), no API key required
licence: CC BY 4.0 (data); API free for non-commercial use under Open-Meteo's terms
terms_notes: CC BY 4.0 permits storing and redistributing the data with attribution. Attribution to Open-Meteo.com is given in the README.
terms_notes: Confirmed against https://open-meteo.com/en/terms. The free API is for non-commercial use only (limits: 600 calls/min, 5,000/hour, 10,000/day, 300,000/month) and data is provided under CC BY 4.0, so attribution to Open-Meteo.com is required. This project is a non-commercial university project and makes a handful of calls per run, far below the limits. No API key is used.
update_cadence: hourly values; underlying weather models update every few hours
coverage: Selected airport locations set in OPENMETEO_LOCATIONS (default: IST, SAW, ESB, ADB, AYT). Hourly variables: wind_speed_10m, wind_gusts_10m, precipitation, showers, cape. Window: past_days + forecast_days (default 1 + 1), timezone UTC.
record_meaning: one row = one weather variable value at one location for one hour (stored as parallel arrays per location in the raw JSON)
join_key: location (latitude/longitude) + hourly timestamp (UTC) -> joins to OpenSky state vectors (nearest location, same hour)
first_retrieved: 2026-10-09T14:53:48Z
known_issues:

- Values are model output (forecast/reanalysis blend), not station observations.
- Coordinates are snapped to the model grid; the returned latitude/longitude can differ slightly from the request.
- Some variables can be null for certain hours or locations.
- Open-Meteo is a forecast API: past values for the window may be updated between runs.
  When we first run the code to call the API we got this error
  ===== RUN SUMMARY =====
  opensky AUTH FAILED Authentication failed (HTTP 401) for https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token. Check the credentials in your .env file. Not retrying.
  openmeteo SKIPPED run aborted after authentication failure
  0/2 sources fetched successfully.
  Run is NOT healthy: see failures above.
  PS C:\Users\ACER\flight-delay-weather-analysis> python src/ingest.py
  [14:52:50Z] Ingestion run started (UTC 2026-10-09T14:52:50Z)
  [14:52:50Z] Fetching opensky ...

because we did some mistake in the .env file but after correcting our mistake we got the data we wanted.
For openmeteo we didnt get any error and got the data succesfully.
