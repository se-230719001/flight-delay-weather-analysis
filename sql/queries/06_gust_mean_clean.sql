-- M4 2.1. Question: the same mean wind gust per airport and overall, on the cleaned layer
--        with the flag filter we would actually use.
-- Grain: one row per airport, plus one row 'ALL'.
-- Source: weather_hourly_clean (M3). Forecast rule (I-20): hours later than their retrieval
--        are excluded, as in 03_weather_hours_clean.sql.
-- Flag filter: a gust counts only when wind_gusts_10m_flag is one of :f1, :f2, :f3
--        (use flag_params(); unused slots are NULL). n_gust says how many hours that is.
-- Clock: not used (no time grouping).
-- Parameters (bound, never formatted): :f1, :f2, :f3
WITH hours AS (
    SELECT m.iata,
           CASE WHEN w.wind_gusts_10m_flag IN (:f1, :f2, :f3) THEN w.wind_gusts_10m END AS gust
    FROM weather_hourly_clean AS w
    JOIN location_airport AS m ON m.location_key = w.location_key
    WHERE datetime(w.observed_at) <= datetime(w.retrieved_at)
)
SELECT iata,
       COUNT(*)              AS n_hours,
       COUNT(gust)           AS n_gust,
       ROUND(AVG(gust), 2)   AS mean_gust_kmh
FROM hours
GROUP BY iata
UNION ALL
SELECT 'ALL', COUNT(*), COUNT(gust), ROUND(AVG(gust), 2)
FROM hours
ORDER BY iata;
