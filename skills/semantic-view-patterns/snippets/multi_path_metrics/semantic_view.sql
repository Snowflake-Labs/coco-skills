-- Multi-Path Metrics: Semantic View DDL

USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE SEMANTIC VIEW SNIPPETS.PUBLIC.FLIGHT_WEATHER_SV

  TABLES (
    flights PRIMARY KEY (flight_id),
    weather PRIMARY KEY (city_code, start_date, end_date)
      UNIQUE (city_code, start_date, end_date)
      CONSTRAINT weather_range DISTINCT RANGE BETWEEN start_date AND end_date EXCLUSIVE
  )

  RELATIONSHIPS (
    -- Two paths from flights to weather — one per city role
    flight_departure_weather AS flights(departure_city, departure_time)
      REFERENCES weather(city_code, BETWEEN start_date AND end_date EXCLUSIVE),
    flight_arrival_weather AS flights(arrival_city, arrival_time)
      REFERENCES weather(city_code, BETWEEN start_date AND end_date EXCLUSIVE)
  )

  DIMENSIONS (
    -- A single physical column (weather_condition) reached via two paths.
    -- Queries using this dimension must pair it with a USING-scoped metric.
    weather.weather_condition AS weather_condition
      WITH SYNONYMS ('weather', 'conditions', 'sky condition')
  )

  METRICS (
    -- Total flights — no disambiguation needed (doesn't use weather dim)
    flights.total_flights AS COUNT(flight_id)
      WITH SYNONYMS ('number of flights', 'flight count'),

    -- Late flights broken down by DEPARTURE weather
    -- USING clause tells the engine: follow flight_departure_weather path
    flights.late_departure_count AS COUNT_IF(is_late)
      USING (flight_departure_weather)
      WITH SYNONYMS ('late departures', 'delayed departures', 'flights late at departure'),

    -- Late flights broken down by ARRIVAL weather
    flights.late_arrival_count AS COUNT_IF(is_late)
      USING (flight_arrival_weather)
      WITH SYNONYMS ('late arrivals', 'delayed arrivals', 'flights late at arrival'),

    -- All flights broken down by departure weather
    flights.departure_flight_count AS COUNT(flight_id)
      USING (flight_departure_weather)
      WITH SYNONYMS ('flights by departure weather'),

    -- All flights broken down by arrival weather
    flights.arrival_flight_count AS COUNT(flight_id)
      USING (flight_arrival_weather)
      WITH SYNONYMS ('flights by arrival weather')
  )

  COMMENT = 'Flight delays analyzed by weather at departure city and weather at arrival city. Uses USING clause to disambiguate two range relationships to the same weather table.'

  AI_SQL_GENERATION 'This SV has two relationships to the weather table: departure weather and arrival weather. Use USING-scoped metrics: late_departure_count/departure_flight_count for departure weather analysis; late_arrival_count/arrival_flight_count for arrival weather analysis. You can combine both in one query to compare departure vs arrival conditions side by side.';
