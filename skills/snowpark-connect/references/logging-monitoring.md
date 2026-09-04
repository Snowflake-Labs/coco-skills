# Snowpark Submit Logging and Monitoring Reference

## Application-Level Logging

```python
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class Timer:
    def __init__(self, name):
        self.name = name
        self.start = None
    
    def __enter__(self):
        self.start = time.time()
        logger.info(f"Starting: {self.name}")
        return self
    
    def __exit__(self, *args):
        elapsed = time.time() - self.start
        logger.info(f"Completed: {self.name} in {elapsed:.2f}s")

with Timer("RGC Geocoding"):
    result_df = process_geocoding(input_df)
```

## Record Count Logging

```python
def log_dataframe_stats(df, stage_name, snowflake_session):
    temp_view = f"_temp_stats_{stage_name.replace(' ', '_')}"
    df.createOrReplaceTempView(temp_view)
    count_result = snowflake_session.sql(f"SELECT COUNT(*) as cnt FROM {temp_view}").collect()
    record_count = count_result[0]['CNT']
    logger.info(f"[{stage_name}] Records: {record_count:,}")
    return record_count
```

## Query Performance Profiling

```python
def execute_and_profile(snowflake_session, sql, description):
    start = time.time()
    result = snowflake_session.sql(sql).collect()
    query_id = snowflake_session.sql("SELECT LAST_QUERY_ID() as qid").collect()[0]['QID']
    elapsed = time.time() - start
    logger.info(f"Completed: {description} in {elapsed:.2f}s (query_id: {query_id})")
    return result, query_id
```

```sql
SELECT 
    operator_type,
    ROUND(operator_statistics:output_rows, 0) as output_rows,
    ROUND(execution_time_breakdown:overall_percentage, 1) as pct_time
FROM TABLE(GET_QUERY_OPERATOR_STATS('<query_id>'))
ORDER BY pct_time DESC
LIMIT 10;
```

## Job Status Monitoring

```sql
SHOW SERVICES LIKE '<WORKLOAD_NAME>%';
SELECT * FROM TABLE(SYSTEM$GET_SERVICE_STATUS('<service_name>'));
```

```bash
snow sql -q "SHOW SERVICES LIKE '${WORKLOAD_NAME}%'" -c "$CONNECTION"
snow sql -q "CALL SYSTEM\$GET_SERVICE_LOGS('<service_name>', 0, 'driver', 100)" -c "$CONNECTION"
```

## Checkpoint Verification

```python
def verify_checkpoint(table_name, snowflake_session):
    result = snowflake_session.sql(f"""
        SELECT COUNT(*) as cnt, COUNT(DISTINCT *) as distinct_cnt 
        FROM {table_name}
    """).collect()
    total = result[0]['CNT']
    distinct = result[0]['DISTINCT_CNT']
    logger.info(f"Checkpoint {table_name}: {total:,} rows ({distinct:,} distinct)")
    if total != distinct:
        logger.warning(f"Potential duplicates: {total - distinct:,} duplicate rows")
    return total
```

## Multi-Stage Progress Tracking

```python
def process_with_progress(df, tiers, snowflake_session):
    total_records = get_count_via_sql(df, snowflake_session)
    processed = 0
    for tier_name, tier_config in tiers.items():
        unmatched = total_records - processed
        if unmatched == 0:
            break
        start = time.time()
        matched_count = execute_tier(tier_config, snowflake_session)
        elapsed = time.time() - start
        processed += matched_count
        pct = (processed / total_records) * 100
        logger.info(f"[{tier_name}] Matched: {matched_count:,} ({pct:.1f}% cumulative) in {elapsed:.2f}s")
```
