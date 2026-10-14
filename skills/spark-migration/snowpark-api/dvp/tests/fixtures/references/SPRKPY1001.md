# SPRKPY1001

Unsupported DataFrame operation

## Resolution

Replace the unsupported operation with Snowpark equivalent:

```python
# Before (PySpark)
df.rdd.map(lambda x: x)

# After (Snowpark)
df.select("*")
```

## Action

1. Identify the unsupported operation
2. Replace with Snowpark equivalent
3. Remove the `#EWI: SPRKPY1001` comment
