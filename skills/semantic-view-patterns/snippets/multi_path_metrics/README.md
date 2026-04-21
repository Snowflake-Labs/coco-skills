# Multi-Path Metrics (USING Clause)

## The Problem

A fact table has **two foreign keys that both point to the same dimension**. You want separate metrics for each path — for example, a flight has both a `departure_city` and an `arrival_city`, and you want to look up the weather conditions at each city at the time of departure/arrival.

Without disambiguation, the SV engine would raise: `Multi-path relationship between dimension entity 'X' and base metric entity 'Y'`.

## How You Might Express This Need

- "Flights have both a departure and arrival airport. I want to break down delays by departure weather AND by arrival weather separately."
- "My orders table has both a ship-to address and a bill-to address, both joining the same address dim. How do I use both?"
- "I want a metric for sales by origin region AND a metric for sales by destination region."

## Equivalent in Other Tools

| Tool | Approach |
|------|----------|
| **SQL** | Two separate JOINs with aliases: `JOIN weather AS dep_weather ON ... JOIN weather AS arr_weather ON ...` |
| **LookML** | `view: departure_weather { extends: [weather_base] }` — role-playing views |
| **dbt** | Two separate `ref()` models or CTEs aliasing the same source |
| **Power BI** | Multiple relationships to the same table; mark one as inactive; use USERELATIONSHIP() |

## The SV Approach

Two mechanisms work together:

**1. Two range relationships** to the same physical table (weather):
```sql
flight_departure_weather AS flights(departure_city, departure_time)
    REFERENCES weather(city_code, BETWEEN start_date AND end_date EXCLUSIVE),
flight_arrival_weather AS flights(arrival_city, arrival_time)
    REFERENCES weather(city_code, BETWEEN start_date AND end_date EXCLUSIVE)
```

**2. USING clause** on each metric to specify which path to follow:
```sql
flights.m_late_departure_count AS COUNT_IF(is_late)
    USING (flight_departure_weather)
    WITH SYNONYMS ('late flights by departure weather'),

flights.m_late_arrival_count AS COUNT_IF(is_late)
    USING (flight_arrival_weather)
    WITH SYNONYMS ('late flights by arrival weather')
```

The `USING` clause tells the engine: "when resolving `weather.weather_condition` for this metric, take the `flight_departure_weather` path."

## Key Rules

- `USING` specifies a **path prefix** from the metric entity to a disambiguating entity
- Without `USING`, querying `weather.weather_condition` with a metric that has two paths to `weather` will error
- Metrics without `USING` cannot be broken down by the ambiguous dimension at all
- Each metric can have a different `USING` path — enabling side-by-side comparison in one query

## Docs

- [Specifying the relationship for a metric when multiple relationship paths exist](https://docs.snowflake.com/en/user-guide/views-semantic/sql#specifying-the-relationship-for-a-metric-when-multiple-relationship-paths-exist)

## Files

| File | Description |
|------|-------------|
| `schema.sql` | `flights` and `weather` table DDL |
| `seed_data.sql` | 4 flights, 5 weather records spanning departure/arrival windows |
| `semantic_view.sql` | SV with two range relationships + USING on each metric |
| `queries.sql` | Departure vs arrival weather breakdown + the error without USING |
