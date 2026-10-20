-- Question: at each weather location (airport), how windy was it, and how much
--           air traffic was reported close to it (within 0.25 degrees, about 28 km)?
-- Result grain: one row per location.
-- Weather and traffic are each aggregated to the location grain in their own CTE
-- BEFORE being joined, so the join (1 row per location on each side) cannot
-- multiply rows. Missing (NULL) values are skipped by AVG/MAX.
WITH weather AS (
    SELECT location_key,
           COUNT(*)            AS weather_hours,
           ROUND(AVG(wind_gusts_10m), 1) AS avg_gust_kmh,
           MAX(wind_gusts_10m) AS max_gust_kmh
    FROM weather_hourly
    GROUP BY location_key
),
traffic AS (
    SELECT l.location_key,
           COUNT(*)                        AS nearby_states,
           COUNT(DISTINCT s.aircraft_key)  AS nearby_aircraft
    FROM location AS l
    JOIN aircraft_state AS s
      ON ABS(s.latitude  - l.latitude)  <= 0.25
     AND ABS(s.longitude - l.longitude) <= 0.25
    GROUP BY l.location_key
)
SELECT l.location_index,
       l.latitude,
       l.longitude,
       w.weather_hours,
       w.avg_gust_kmh,
       w.max_gust_kmh,
       COALESCE(t.nearby_states, 0)   AS nearby_states,
       COALESCE(t.nearby_aircraft, 0) AS nearby_aircraft
FROM location AS l
JOIN weather AS w ON w.location_key = l.location_key
LEFT JOIN traffic AS t ON t.location_key = l.location_key
ORDER BY w.avg_gust_kmh DESC;