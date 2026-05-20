# SCOS Performance Optimization Reference

Detailed code patterns and SQL examples for optimizing PySpark pipelines on Snowpark Connect (SCOS).

## Tiered Join Strategy (Geohash)

For large-scale geocoding or spatial lookups, use tiered filtering to minimize expensive operations:

```python
def build_tiered_geocoding(snowflake_session, gps_table, cities_table):
    """
    Tiered reverse geocoding: try tightest filter first, 
    fall back to looser filters only for unmatched records.
    """
    
    tiers = [
        ("Tier1_GH4", 4, 39),    # geohash4: ~39km, ~5x expansion
        ("Tier2_GH3", 3, 156),   # geohash3: ~156km, ~21x expansion  
        ("Tier3_GH2", 2, 625),   # geohash2: ~625km, ~100x expansion
        ("Tier4_CROSS", 0, None) # Cross join fallback (rare)
    ]
    
    snowflake_session.sql(f"""
        CREATE OR REPLACE TABLE geocoded_results AS
        SELECT *, NULL::VARCHAR as city, NULL::VARCHAR as state, 
               NULL::FLOAT as distance_km, NULL::INT as match_tier
        FROM {gps_table} WHERE 1=0
    """).collect()
    
    unmatched_count = get_count_sql(gps_table, snowflake_session)
    
    for tier_name, geohash_level, grid_km in tiers:
        if unmatched_count == 0:
            print(f"All records matched! Skipping {tier_name} and remaining tiers.")
            break
            
        print(f"[{tier_name}] Processing {unmatched_count:,} unmatched records (grid: {grid_km}km)...")
        
        if geohash_level > 0:
            join_sql = f"""
                INSERT INTO geocoded_results
                SELECT g.*, c.city, c.state, 
                       HAVERSINE(g.lat, g.lon, c.latitude, c.longitude) as distance_km,
                       {geohash_level} as match_tier
                FROM unmatched_gps g
                INNER JOIN {cities_table} c
                  ON SUBSTRING(ST_GEOHASH(ST_POINT(g.lon, g.lat), 10), 1, {geohash_level}) 
                     = c.geohash{geohash_level}
                QUALIFY ROW_NUMBER() OVER (PARTITION BY g.id ORDER BY distance_km) = 1
            """
        else:
            join_sql = f"""
                INSERT INTO geocoded_results
                SELECT g.*, c.city, c.state,
                       HAVERSINE(g.lat, g.lon, c.latitude, c.longitude) as distance_km,
                       0 as match_tier
                FROM unmatched_gps g
                CROSS JOIN {cities_table} c
                QUALIFY ROW_NUMBER() OVER (PARTITION BY g.id ORDER BY distance_km) = 1
            """
        
        snowflake_session.sql(join_sql).collect()
        unmatched_count = get_unmatched_count(snowflake_session)
```

## Dynamic SQL Building

```python
def build_tier_query(geohash_level, source_table, lookup_table, output_columns):
    """Build geohash-filtered join query for a specific tier."""
    
    geohash_col = f"geohash{geohash_level}"
    gps_geohash = f'SUBSTRING(ST_GEOHASH(ST_POINT(d."longitude", d."latitude"), 10), 1, {geohash_level})'
    
    col_list = ", ".join([f'd."{c}"' for c in output_columns])
    
    return f"""
        SELECT {col_list},
               c."city" as "CITY",
               c."state" as "STATE", 
               c."country" as "COUNTRY",
               HAVERSINE(d."latitude", d."longitude", c."latitude", c."longitude") as "DISTANCE_KM",
               ROW_NUMBER() OVER (PARTITION BY d."id" ORDER BY "DISTANCE_KM") as "RN"
        FROM {source_table} d
        INNER JOIN {lookup_table} c ON c."{geohash_col}" = {gps_geohash}
        QUALIFY "RN" = 1
    """
```

## Performance Monitoring Utilities

### Capture Query IDs

```python
def execute_with_profiling(snowflake_session, sql, description):
    """Execute SQL and return query ID for profiling."""
    import time
    
    start = time.time()
    result = snowflake_session.sql(sql).collect()
    
    query_id = snowflake_session.sql("SELECT LAST_QUERY_ID()").collect()[0][0]
    elapsed = time.time() - start
    
    print(f"[{description}] Completed in {elapsed:.2f}s (query_id: {query_id})")
    return result, query_id
```

### Analyze Bottlenecks

```python
def analyze_query_profile(query_id, snowflake_session):
    """Get top operators by execution time."""
    
    profile_sql = f"""
        SELECT 
            operator_type,
            ROUND(operator_statistics:output_rows, 0) as output_rows,
            ROUND(execution_time_breakdown:overall_percentage, 1) as pct_time
        FROM TABLE(GET_QUERY_OPERATOR_STATS('{query_id}'))
        ORDER BY pct_time DESC
        LIMIT 5
    """
    
    results = snowflake_session.sql(profile_sql).collect()
    
    print(f"\nQuery Profile for {query_id}:")
    print("-" * 50)
    for row in results:
        print(f"  {row['OPERATOR_TYPE']:20} | {row['OUTPUT_ROWS']:>12,} rows | {row['PCT_TIME']:>5}%")
```

## Lookup Table Setup

```sql
CREATE TABLE CITIES_LOOKUP (
    city VARCHAR(200),
    latitude FLOAT,
    longitude FLOAT,
    geohash2 VARCHAR(2),
    geohash3 VARCHAR(3),
    geohash4 VARCHAR(4)
);

ALTER TABLE CITIES_LOOKUP CLUSTER BY (geohash4, geohash3, geohash2);
```

## Spatial Functions Quick Reference

```sql
ST_GEOHASH(ST_POINT(longitude, latitude), precision)
SUBSTRING(ST_GEOHASH(ST_POINT(lon, lat), 10), 1, 4)  -- geohash4

HAVERSINE(lat1, lon1, lat2, lon2)  -- Returns km
```

## Common Error Messages and Fixes

### "Insert value list does not match column list"

**Cause:** SELECT produces different number/order of columns than target table.

**Fix:** Explicitly list columns in both INSERT and SELECT:

```sql
INSERT INTO target_table (col1, col2, col3)
SELECT col1, col2, col3 FROM source
```

### "Numeric value 'SomeText' is not recognized"

**Cause:** Column order mismatch - text value going into numeric column.

**Fix:** Verify SELECT column order matches INSERT column order exactly.

### "Invalid identifier 'X.COLUMN_NAME'"

**Cause:** Referencing column that doesn't exist in that scope (e.g., CTE alias).

**Fix:** Compute values inline or ensure column exists in referenced table/CTE.

### "Object 'X' does not exist or not authorized"

**Cause:** Case sensitivity - table created with quotes but accessed without.

**Fix:** Use consistent quoting or check actual table name with `SHOW TABLES LIKE '%name%'`.
