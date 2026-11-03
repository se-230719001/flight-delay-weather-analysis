-- M4. Question: how many weather hours exist, per airport and UTC day, against the 24 that
--        could exist, and how many of them carry a usable gust value under the flag filter?
-- Grain: one row per (airport, UTC date).
-- Source: weather_hourly_clean (M3). Same forecast rule and flag filter as 03_weather_hours_clean.sql.
-- Clock: UTC DATES. This is a data-supply table (what was stored when), not a statement about
--        human behaviour. Local-day coverage is computed in the notebook after tz_convert.
-- Parameters (bound, never formatted): :f1, :f2, :f3
SELECT m.iata,
       substr(w.observed_at, 1, 10) AS utc_date,
       COUNT(*)                     AS n_hours,
       SUM(w.wind_gusts_10m_flag IN (:f1, :f2, :f3)) AS n_gust_usable,
       MIN(w.observed_at)           AS first_hour,
       MAX(w.observed_at)           AS last_hour
FROM weather_hourly_clean AS w
JOIN location_airport AS m ON m.location_key = w.location_key
WHERE datetime(w.observed_at) <= datetime(w.retrieved_at)
GROUP BY m.iata, substr(w.observed_at, 1, 10)
ORDER BY m.iata, utc_date;
