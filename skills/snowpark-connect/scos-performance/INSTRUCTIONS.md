
# SCOS Performance Optimization

Optimize PySpark applications running on Snowflake via Snowpark Connect (SCOS).

## When to Use

- SCOS job is slow or hanging
- Out of memory errors with `.count()`, `.collect()`, or `.cache()`
- Cross join or cartesian product detected
- WindowFunction taking excessive time
- Column name / case sensitivity issues
- Need checkpointing strategy for complex pipelines

## Quick Diagnostics

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| Job hangs indefinitely | `.count()` or `.collect()` on large data | Use SQL COUNT via SnowflakeSession |
| WindowFunction takes 50%+ time | Join expansion before ROW_NUMBER | Add tighter pre-filters (e.g., geohash4 vs geohash3) |
| Out of memory errors | `.cache()` or `.toPandas()` | Checkpoint to temp tables |
| Column names wrong/uppercase | Case sensitivity mismatch | Set `spark.sql.caseSensitive=true` |
| INSERT column mismatch | SELECT order doesn't match table | Explicitly order columns in SELECT |
| Query runs forever | CROSS JOIN or cartesian product | Add join keys or pre-filter data |

## Workflow

### Step 1: Identify the Problem

**Ask** the user to describe the performance issue:

```
What performance issue are you seeing?

1. Job is slow or hanging
2. Out of memory errors
3. Cross join / cartesian product
4. Column name or case sensitivity issues
5. Need checkpointing strategy
6. General optimization review
```

**⚠️ MANDATORY STOPPING POINT**: Wait for user response before proceeding.

### Step 2: Collect Context

**Gather** the following from the user:
- The slow query or code snippet
- Approximate data sizes involved
- Current warehouse size
- Any query IDs from Snowflake query history

### Step 3: Apply Targeted Fix

**Route based on problem category:**

#### 3a: Memory Issues (count/collect/cache)

The three anti-patterns to eliminate:

```python
# AVOID on large datasets:
bad_count = df.count()       # Pulls count through Spark
bad_data = df.collect()      # Pulls ALL data to driver
df.cache()                   # Stores in Spark memory
```

**Safe alternatives:**

```python
from snowflake.snowpark_connect.snowflake_session import SnowflakeSession
snowflake_session = SnowflakeSession(spark)

# Count via SQL
def get_count(df, snowflake_session, temp_name="_temp_count"):
    df.createOrReplaceTempView(temp_name)
    result = snowflake_session.sql(f"SELECT COUNT(*) as cnt FROM {temp_name}").collect()
    return result[0]['CNT']

# Checkpoint to temp table (not cache)
def checkpoint_dataframe(df, table_name, snowflake_session):
    df.createOrReplaceTempView("_checkpoint_source")
    snowflake_session.sql(f"""
        CREATE OR REPLACE TABLE {table_name} AS 
        SELECT * FROM _checkpoint_source
    """).collect()
    return spark.table(table_name)
```

#### 3b: Join Optimization (Cross Join Problem)

**Problem:** Joining without proper keys creates cartesian products.

```python
# TERRIBLE: 10M GPS × 25K cities = 250 BILLION rows
bad = gps_df.crossJoin(cities_df).filter(distance < 50)
```

**Solution:** Pre-filter with spatial keys (geohash):

```sql
SELECT g.*, c.city, c.state,
       HAVERSINE(g.lat, g.lon, c.latitude, c.longitude) as distance_km
FROM gps_data g
INNER JOIN cities_lookup c 
  ON SUBSTRING(ST_GEOHASH(ST_POINT(g.lon, g.lat), 10), 1, 4) = c.geohash4
```

**Geohash grid sizes:**

| Precision | Grid Size | Join Expansion | Use Case |
|-----------|-----------|----------------|----------|
| geohash2 | ~625 km | ~100x | Continental fallback |
| geohash3 | ~156 km | ~21x | Regional matching |
| geohash4 | ~39 km | ~5x | City-level (recommended start) |
| geohash5 | ~5 km | ~1-2x | Neighborhood precision |

For full tiered join implementation, **read** `references/performance-patterns.md`.

#### 3c: WindowFunction Optimization

`ROW_NUMBER() OVER (...)` cost scales with input rows. If your join expands 21x, WindowFunction processes 21x more rows.

**Analyze with query profile:**

```sql
SELECT operator_type,
    ROUND(operator_statistics:output_rows, 0) as output_rows,
    ROUND(execution_time_breakdown:overall_percentage, 1) as pct_time
FROM TABLE(GET_QUERY_OPERATOR_STATS('<query_id>'))
WHERE operator_type = 'WindowFunction'
ORDER BY pct_time DESC;
```

**Solutions:**
1. Use tighter join keys (geohash4 vs geohash3)
2. Pre-filter before ROW_NUMBER
3. Use QUALIFY instead of subquery (Snowflake optimizes this)

```sql
-- GOOD: QUALIFY
SELECT * FROM joined_data
QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY distance) = 1

-- AVOID: Subquery
SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (...) as rn FROM joined_data
) WHERE rn = 1
```

#### 3d: Case Sensitivity

```python
# ALWAYS set this in SparkSession
spark = SparkSession.builder \
    .config("spark.sql.caseSensitive", "true") \
    .getOrCreate()
```

Without this, column `"userId"` becomes `USERID`, joins fail silently, and INSERT statements fail with column mismatch.

#### 3e: Checkpointing

**When to checkpoint:**
- After expensive transformations (joins, aggregations)
- Before operations that re-read data
- At logical pipeline stages

```python
# PREFER: SQL CTAS (faster, preserves column case)
def checkpoint_with_ctas(df, table_name, snowflake_session):
    df.createOrReplaceTempView(f"_ckpt_{table_name}")
    snowflake_session.sql(f"""
        CREATE OR REPLACE TABLE {table_name} AS
        SELECT * FROM _ckpt_{table_name}
    """).collect()
    return spark.table(table_name)

# AVOID: DataFrame.write (slower, case issues)
df.write.mode("overwrite").saveAsTable(table_name)
```

**For INSERT column matching:**

```python
def get_table_columns(table_name, snowflake_session):
    result = snowflake_session.sql(f"""
        SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS 
        WHERE TABLE_NAME = '{table_name}' ORDER BY ORDINAL_POSITION
    """).collect()
    return [row['COLUMN_NAME'] for row in result]
```

### Step 4: Verify Improvement

**Run** the optimized query and compare:

1. Capture query ID before and after optimization
2. Compare execution times
3. Check WindowFunction row counts in query profile

For profiling utilities, **read** `references/performance-patterns.md`.

**⚠️ MANDATORY STOPPING POINT**: Present before/after comparison to user.

## SnowflakeSession Bridge Pattern

**Use for all direct SQL operations on Snowflake:**

```python
from snowflake.snowpark_connect.snowflake_session import SnowflakeSession
snowflake_session = SnowflakeSession(spark)
result = snowflake_session.sql("SELECT COUNT(*) FROM my_table").collect()
```

Benefits: Executes in Snowflake warehouse (fast), no data transfer to Spark driver, full Snowflake SQL support.

## Reading Tables

```python
# CORRECT: SCOS-compliant
df = spark.table("DATABASE.SCHEMA.TABLE_NAME")

# AVOID: Not needed in SCOS
df = spark.read.format("snowflake").load()
```

## Stopping Points

- ✋ After Step 1: Wait for user to describe the performance issue
- ✋ After Step 4: Present optimization results to user

## Optimization Checklist

- [ ] `spark.sql.caseSensitive=true` is set
- [ ] No `.count()`, `.collect()`, or `.cache()` on large DataFrames
- [ ] Joins have proper keys (no accidental CROSS JOINs)
- [ ] Lookup tables have appropriate clustering
- [ ] Checkpoint tables created for complex pipelines
- [ ] Column order verified for INSERT statements
- [ ] Query IDs captured for post-run analysis

## Output

- Optimized SCOS pipeline with reduced execution time
- Query profiles identifying remaining bottlenecks
- Checkpointed intermediate tables for debugging
