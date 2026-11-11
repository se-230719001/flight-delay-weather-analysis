-- M4 2.1. Question: what is the mean wind gust at each airport, and over all five, computed
--        on the M2 table exactly as loaded (no flags, no cleaning)?
-- Grain: one row per airport, plus one row 'ALL' (every hour of every airport pooled).
-- Source: weather_hourly (M2, raw). Flag filter: NONE (raw has no flags).
-- :observed_only = 0 keeps every stored hour, forecast hours included (the M2 view);
--                  1 drops hours later than the retrieval that supplied them (I-20), so the
--                  effect of the forecast rule can be separated from the effect of the flags.
-- Clock: not used (no time grouping).
-- Parameters (bound, never formatted): :observed_only
WITH hours AS (
    SELECT m.iata, w.wind_gusts_10m
    FROM weather_hourly AS w
    JOIN location_airport AS m ON m.location_key = w.location_key
    WHERE :observed_only = 0
       OR datetime(w.observed_at) <= datetime(w.retrieved_at)
)
SELECT iata,
       COUNT(*)                      AS n_hours,
       COUNT(wind_gusts_10m)         AS n_gust,
       ROUND(AVG(wind_gusts_10m), 2) AS mean_gust_kmh
FROM hours
GROUP BY iata
UNION ALL
SELECT 'ALL', COUNT(*), COUNT(wind_gusts_10m), ROUND(AVG(wind_gusts_10m), 2)
FROM hours
ORDER BY iata;
