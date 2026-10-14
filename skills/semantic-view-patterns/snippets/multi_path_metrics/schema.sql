-- Multi-Path Metrics: Schema

CREATE DATABASE IF NOT EXISTS SNIPPETS;
CREATE SCHEMA IF NOT EXISTS SNIPPETS.PUBLIC;
USE DATABASE SNIPPETS;
USE SCHEMA PUBLIC;

CREATE OR REPLACE TABLE flights (
    flight_id       INTEGER     NOT NULL,
    departure_city  VARCHAR(10) NOT NULL,
    arrival_city    VARCHAR(10) NOT NULL,
    is_late         BOOLEAN     NOT NULL,
    departure_time  TIMESTAMP_NTZ NOT NULL,
    arrival_time    TIMESTAMP_NTZ NOT NULL
);

CREATE OR REPLACE TABLE weather (
    city_code         VARCHAR(10) NOT NULL,
    weather_condition VARCHAR(20) NOT NULL,
    start_date        TIMESTAMP_NTZ NOT NULL,
    end_date          TIMESTAMP_NTZ NOT NULL
);
