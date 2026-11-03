-- M4. Question: which weather hours can the exploration trust, and what were their values?
-- Grain: one row per (airport, hour), the grain of weather_hourly_clean.
-- Source: weather_hourly_clean (M3).
-- Forecast rule (I-20): an hour later than the retrieval that supplied it is a forecast, not
--        an observation, and is EXCLUDED. datetime() is used on both sides because the two
--        timestamp texts have different formats ('...T10:00Z' vs '...T10:07:07Z'), so a plain
--        text comparison can be wrong.
-- Flag filter: a gust value is returned only when wind_gusts_10m_flag is one of the three
--        allowed flags :f1, :f2, :f3 (pass the same flag twice or NULL to allow fewer).
--        Otherwise wind_gusts_10m is NULL but the row stays, so denominators remain visible.
--        gust_usable says which case applied. The other variables use their 'ok' rows only.
-- Clock: observed_at is UTC text; convert to local time in the notebook.
-- Parameters (bound, never formatted): :f1, :f2, :f3
SELECT m.iata,
       w.observed_at,
       w.retrieved_at,
       w.wind_gusts_10m_flag,
       CASE WHEN w.wind_gusts_10m_flag IN (:f1, :f2, :f3) THEN 1 ELSE 0 END AS gust_usable,
       CASE WHEN w.wind_gusts_10m_flag IN (:f1, :f2, :f3) THEN w.wind_gusts_10m END AS wind_gusts_10m,
       CASE WHEN w.wind_speed_10m_flag = 'ok' THEN w.wind_speed_10m END AS wind_speed_10m,
       CASE WHEN w.precipitation_flag = 'ok' THEN w.precipitation END AS precipitation,
       w.cape_flag,
       CASE WHEN w.cape_flag IN ('ok', 'unusual_high') THEN w.cape END AS cape
FROM weather_hourly_clean AS w
JOIN location_airport AS m ON m.location_key = w.location_key
WHERE datetime(w.observed_at) <= datetime(w.retrieved_at)
ORDER BY m.iata, w.observed_at;
