# SPRKCNTPY1001

SparkSession has been replaced with Session in Snowpark Connect

Message: The SparkSession creation has been transformed to use a Snowpark Session equivalent.

Category: Warning.

## Description

This issue appears when the SMA detects the creation of a SparkSession object in the input code. Snowpark Connect uses a different object, called Session, to manage the connection to Snowflake and to create DataFrames.
When the SMA encounters the creation of a SparkSession, it adds this EWI to inform you that it has transformed the code to use a Snowpark Session instead.

## Scenario

**Input**

Below is an example of a Python SparkSession initialization which will be replaced for a Snowpark Connect Session initialization, and therefore it generates this EWI.

```python
spark = SparkSession.builder.getOrCreate()
```

**Output**

The SMA adds the EWI `SPRKCNTPY1001` to the output code to let you know that your SparkSession initialization has been replaced for a Snowpark Connect Session initialization.

```python
#EWI: SPRKCNTPY1001 => The creation of the SparkSession has been replaced with the creation of an equivalent Snowpark Connect Session.
spark = snowpark_connect.server.init_spark_session()
```

**Recommended fix**



## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
