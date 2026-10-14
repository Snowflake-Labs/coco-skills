# PNDSPY1260

Element has a partial mapping but there are a few scenarios not supported in Snowpark.

Message: Pandas < ***pandas.io.json._json.read_json*** > has a partial mapping with a few scenarios not supported in Snowpark.

Category: Warning.

## Description

This issue appears when the SMA detects the use of a Pandas element that has a direct equivalent in Snowpark Pandas, but some scenarios might behave differently than Pandas.


**Missing or Unsupported Parameters:** ``orient``, ``typ``, ``dtype``, ``convert_axes``, ``lines``, ``convert_dates``, ``date_unit``, ``keep_default_dates``, ``encoding_errors``, ``nrows``, ``and chunksize will raise an error. precise_float``, ``engine``, ``dtype_backend``, ``and storage_options are ignored.``

**Reason (from Snowpark documentation):** P: - if ndjson files are passed - Supported parameters are compression and encoding

## Scenario

**Input**

Below is an example of a method with a few scenarios not supported in Snowpark.

```python
import pandas as pd

df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = pd.read_json(df)
```

**Output**

The SMA adds the EWI `PNDSPY1260` on the output code to let you know that this element has a few scenarios not supported in Snowpark.

```python
import snowflake.snowpark.modin.pandas as pd

#EWI: PNDSPY1260 => pandas.io.json._json.read_json has a partial mapping, with few scenarios not supported. Check Snowpark Pandas documentation for more detail.
df = pd.DataFrame({'A': [1, 2, 3], 'B': [4, 5, 6]})
result = pd.read_json(df)
```

**Recommended fix**

The following parameters are not supported in Snowpark Pandas: `orient`, `typ`, `dtype`, `convert_axes`, `lines`, `convert_dates`, `date_unit`, `keep_default_dates`, `encoding_errors`, `nrows`, `and chunksize will raise an error. precise_float`, `engine`, `dtype_backend`, `and storage_options are ignored.`.

**Recommended approaches:**

1. **Avoid unsupported parameters**: Modify your code to not use these parameters if they are not essential.

2. **Use `.to_pandas()` for full compatibility**: If you need these parameters, convert to native Pandas first:
   ```python
   # Convert to native pandas when unsupported parameters are needed
   native_df = df.to_pandas()
   result = native_df.read_json(...)  # Use all parameters
   ```

3. **Split the operation**: Perform supported operations in Snowpark Pandas, then use native Pandas only for the unsupported functionality.

**Behavioral note**: P: - if ndjson files are passed - Supported parameters are compression and encoding

This behavior may differ from native Pandas. Recommended actions:
- Test with a representative sample of your data
- Compare results with native Pandas if precision is critical
- Use `.to_pandas()` if exact Pandas behavior is required

## Additional recommendations

- Check the [Snowpark Pandas documentation](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/modin/supported/index) to verify which scenarios are not supported for that specific element.

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
