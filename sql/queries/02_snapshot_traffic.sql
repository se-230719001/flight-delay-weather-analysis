-- M4. Question: how much airborne traffic was reported near each airport at each ADS-B snapshot?
-- Grain: one row per (snapshot, airport). EVERY snapshot x airport pair appears, including
--        pairs with no aircraft (n_aircraft = 0): zeros are observations, not missing rows.
-- Source: aircraft_state_clean (M3). Flag filter: position columns must be 'ok'
--        (latitude_flag = 'ok' AND longitude_flag = 'ok'); velocity is used only when
--        velocity_flag = 'ok'.
-- Assignment rule: a state belongs to the NEAREST airport whose (scaled) distance is within
--        :radius_deg, so a state in the overlap of two airport areas is counted once.
--        distance^2 = dlat^2 + (dlon * :lon_scale)^2;  :lon_scale ~ cos(40.5 deg) = 0.76,
--        so :radius_deg = 0.25 is a circle of about 28 km. Weather grid points are used as
--        airport positions (location.latitude / longitude), as in M2.
-- Clock: snapshot = retrieved_at (UTC, text). hour_utc is the UTC hour it falls in; it joins
--        to weather_hourly_clean.observed_at. Convert to local time in the notebook (tz_convert).
-- Caveat: M2 keeps a repeated state (same icao24 and time_position) only in its latest
--        snapshot, so a state seen in two snapshots is counted in the later one only
--        (12 of 802 states in the first four files).
-- Parameters (bound, never formatted): :radius_deg, :lon_scale
WITH snapshots AS (
    SELECT DISTINCT retrieved_at AS snapshot
    FROM aircraft_state_clean
),
grid AS (
    SELECT s.snapshot,
           substr(s.snapshot, 1, 13) || ':00Z' AS hour_utc,
           l.location_key,
           m.iata
    FROM snapshots AS s
    CROSS JOIN location AS l
    JOIN location_airport AS m ON m.location_key = l.location_key
),
candidates AS (
    SELECT c.retrieved_at AS snapshot,
           c.aircraft_key,
           c.time_position,
           c.on_ground,
           CASE WHEN c.velocity_flag = 'ok' THEN c.velocity END AS velocity,
           l.location_key,
           (c.latitude - l.latitude) * (c.latitude - l.latitude)
             + (c.longitude - l.longitude) * :lon_scale * (c.longitude - l.longitude) * :lon_scale
             AS dist2
    FROM aircraft_state_clean AS c
    JOIN location AS l
      ON (c.latitude - l.latitude) * (c.latitude - l.latitude)
         + (c.longitude - l.longitude) * :lon_scale * (c.longitude - l.longitude) * :lon_scale
         <= :radius_deg * :radius_deg
    WHERE c.latitude_flag = 'ok' AND c.longitude_flag = 'ok'
),
nearest AS (
    SELECT snapshot, aircraft_key, time_position, on_ground, velocity, location_key,
           ROW_NUMBER() OVER (PARTITION BY snapshot, aircraft_key, time_position
                              ORDER BY dist2, location_key) AS rank_
    FROM candidates
),
traffic AS (
    SELECT snapshot, location_key,
           COUNT(*)                                     AS n_aircraft,
           SUM(on_ground = 0)                           AS n_airborne,
           SUM(on_ground = 1)                           AS n_ground,
           COUNT(CASE WHEN on_ground = 0 THEN velocity END) AS n_velocity_airborne,
           AVG(CASE WHEN on_ground = 0 THEN velocity END)   AS mean_velocity_airborne
    FROM nearest
    WHERE rank_ = 1
    GROUP BY snapshot, location_key
)
SELECT g.snapshot,
       g.hour_utc,
       g.iata,
       COALESCE(t.n_aircraft, 0)        AS n_aircraft,
       COALESCE(t.n_airborne, 0)        AS n_airborne,
       COALESCE(t.n_ground, 0)          AS n_ground,
       COALESCE(t.n_velocity_airborne, 0) AS n_velocity_airborne,
       t.mean_velocity_airborne         AS mean_velocity_airborne
FROM grid AS g
LEFT JOIN traffic AS t
       ON t.snapshot = g.snapshot AND t.location_key = g.location_key
ORDER BY g.snapshot, g.iata;
