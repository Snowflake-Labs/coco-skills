-- Multi-Path Metrics: Queries

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

-- ============================================================
-- WORKING QUERIES
-- ============================================================

-- 1. Late flights by DEPARTURE weather condition
--    (uses departure_flight_count/late_departure_count → flight_departure_weather path)
--
--    Expected: sunny=2 total (2 late), rainy=2 total (0 late)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.FLIGHT_WEATHER_SV
    DIMENSIONS weather.weather_condition
    METRICS flights.departure_flight_count, flights.late_departure_count
);


-- 2. Late flights by ARRIVAL weather condition
--    (uses arrival_flight_count/late_arrival_count → flight_arrival_weather path)
--
--    Expected: rainy=1 (1 late), sunny=1 (0 late), cloudy=2 (1 late)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.FLIGHT_WEATHER_SV
    DIMENSIONS weather.weather_condition
    METRICS flights.arrival_flight_count, flights.late_arrival_count
);


-- 3. Total flights (no weather dim — no disambiguation needed)
SELECT * FROM SEMANTIC_VIEW(
    SNIPPETS.PUBLIC.FLIGHT_WEATHER_SV
    METRICS flights.total_flights
);


-- ============================================================
-- WHAT DOESN'T WORK
-- ============================================================

-- ERROR: Combining a non-USING metric with the ambiguous weather dimension
-- The engine can't determine which of the two paths to use for total_flights
-- when grouped by weather.weather_condition.
--
-- SELECT * FROM SEMANTIC_VIEW(
--     SNIPPETS.PUBLIC.FLIGHT_WEATHER_SV
--     DIMENSIONS weather.weather_condition
--     METRICS flights.total_flights       -- no USING — ambiguous path to weather
-- );
-- Error: Multi-path relationship between dimension entity 'WEATHER'
--        and base metric entity 'FLIGHTS'

-- NOTE: You CANNOT mix a departure-USING metric with an arrival-USING metric
-- in the same query and group by weather.weather_condition — the dimension
-- would resolve differently for each metric, which is undefined behavior.
-- Instead, run two separate queries or use subqueries.
