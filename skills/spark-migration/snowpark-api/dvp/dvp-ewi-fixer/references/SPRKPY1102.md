# SPRKPY1102

pyspark.sql.pandas.map_ops.PandasMapOpsMixin.mapInPandas

Message: pyspark.sql.pandas.map_ops.PandasMapOpsMixin.mapInPandas has a workaround

Category: Warning

## Description

This issue appears when the tool detects the usage of [pyspark.sql.pandas.map_ops.PandasMapOpsMixin.mapInPandas](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.DataFrame.mapInPandas.html) which has a workaround.
Instead of using the mentioned method, you can use [Snowflake.snowpark.dataframe.map_in_pandas](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.dataframe.map_in_pandas) which is a VERY SIMILAR DataFrame method in Snowpark with nearly identical functionality.

Equivalents between Snowpark and PySpark:

| Aspect            | Snowpark                                   | PySpark                                  |
|-------------------|--------------------------------------------|------------------------------------------|
| Method            | map_in_pandas(dataframe, func, schema, ...) | DataFrame.mapInPandas(func, schema, ...) |
| Function parameter | func: Callable (iterator of pandas DataFrames) | func: Callable (iterator of pandas DataFrames) |
| Schema parameter   | schema: Union\[StructType, str\]          | schema: Union\[StructType, str\]         |
| Purpose            | Applies function to batches using pandas DataFrames | Applies function to batches using pandas DataFrames |
| Iterator pattern   | ✅ Takes iterator, yields DataFrames       | ✅ Takes iterator, yields DataFrames      |



## Scenarios

Each scenario can be different depending on the parameters passed to the method.

### Scenario 1

**Input**

Basic row filtering.

```python
def filter_func(iterator):
    for pdf in iterator:
        yield pdf[pdf.age > 25]

result_df = df.mapInPandas(filter_func, df.schema)
```

**Output**

The tool adds the EWI `SPRKPY1102` indicating that a workaround can be implemented.

```python
def filter_func(iterator):
    for pdf in iterator:
        yield pdf[pdf.age > 25]

#EWI: SPRKPY1102 => pyspark.sql.pandas.map_ops.PandasMapOpsMixin.mapInPandas has a workaround, see documentation for more info
result_df = df.mapInPandas(filter_func, df.schema)
```

**Recommended fix**

As a workaround, you can use [DataFrame.map_in_pandas](https://docs.snowflake.com/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.dataframe.DataFrame.map_in_pandas) instead of `DataFrame.mapInPandas`.

```python
def filter_func(iterator):
    for pdf in iterator:
        yield pdf[pdf.age > 25]

result_df = map_in_pandas(df, filter_func, df.schema)
```

### Scenario 2

**Input**

Here's a simple scenario using PySpark's barrier parameter that cannot be replicated in Snowpark:

```python
def synchronized_processing(iterator):
    """
    All partitions wait for each other before proceeding
    """
    from pyspark import BarrierTaskContext
    import time

    context = BarrierTaskContext.get()
    partition_id = context.partitionId()

    for pdf in iterator:
        # Simulate some processing time
        time.sleep(1)

        # All partitions must reach this point before any can continue
        context.barrier()

        # Now all partitions proceed together
        pdf['processing_time'] = time.time()
        pdf['partition_id'] = partition_id
        pdf['synchronized'] = True

        yield pdf

schema = "id: bigint, value: double, processing_time: double, partition_id: int, synchronized: boolean"

# PySpark: All partitions coordinate and wait for each other
result_df = df.repartition(3).mapInPandas(
    synchronized_processing,
    schema,
    barrier=True  # This parameter does NOT exist in Snowpark
)
```

**Output**

The tool adds the EWI `SPRKPY1102` indicating that a workaround can be implemented.

```python
def synchronized_processing(iterator):
    """
    All partitions wait for each other before proceeding
    """
    from pyspark import BarrierTaskContext
    import time

    context = BarrierTaskContext.get()
    partition_id = context.partitionId()

    for pdf in iterator:
        # Simulate some processing time
        time.sleep(1)

        # All partitions must reach this point before any can continue
        context.barrier()

        # Now all partitions proceed together
        pdf['processing_time'] = time.time()
        pdf['partition_id'] = partition_id
        pdf['synchronized'] = True

        yield pdf

schema = "id: bigint, value: double, processing_time: double, partition_id: int, synchronized: boolean"

# PySpark: All partitions coordinate and wait for each other
#EWI: SPRKPY1102 => pyspark.sql.pandas.map_ops.PandasMapOpsMixin.mapInPandas has a workaround, see documentation for more info
result_df = df.repartition(3).mapInPandas(
    synchronized_processing,
    schema,
    barrier=True  # This parameter does NOT exist in Snowpark
)
```

**Recommended fix**

The recommended fix involves replacing the PySpark mapInPandas method with Snowpark's map_in_pandas method. Since Snowpark does not support the barrier parameter (used for synchronizing partitions in PySpark), the fix modifies the logic to remove synchronization between partitions.

- Key Changes:

  - Remove Synchronization:

    - The BarrierTaskContext and context.barrier() logic (used for partition synchronization in PySpark) is removed because Snowpark does not support this feature.
    Independent Partition Processing:

  - Each partition processes independently without waiting for others. This is achieved by removing the synchronization logic and adding unique identifiers (e.g., execution_id) to distinguish outputs from different partitions.
  Snowpark Method:

  - Replace DataFrame.mapInPandas with map_in_pandas, which is the equivalent method in Snowpark.

- Limitations:

  - The lack of the barrier parameter in Snowpark means that the partitions cannot coordinate or synchronize their execution, which may lead to different behavior compared to the original PySpark implementation.

```python
def non_synchronized_processing(iterator):
    """
    Each partition processes independently - no coordination
    """
    import time
    import uuid

    for pdf in iterator:
        # Each partition processes at its own pace
        time.sleep(1)

        # No way to synchronize with other partitions
        pdf['processing_time'] = time.time()
        pdf['execution_id'] = str(uuid.uuid4())[:8]
        pdf['synchronized'] = False  # Cannot synchronize

        yield pdf

schema = "id: bigint, value: double, processing_time: double, execution_id: string, synchronized: boolean"

# Snowpark: No barrier parameter - each partition runs independently
result_df = map_in_pandas(df, non_synchronized_processing, schema)
# No way to make partitions wait for each other
```


## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
