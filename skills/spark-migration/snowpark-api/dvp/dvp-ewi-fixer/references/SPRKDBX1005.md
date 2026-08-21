# SPRKDBX1005

The method '<***element***>' has an equivalent in Snowflake/Snowsight; however, the element's parameter, a URL path, is not supported.

Message: The method '<***element***>' has an equivalent in Snowflake/Snowsight; however, the element's parameter, a URL path, is not supported.
Category: Warning.

## Description

This issue appears when the SMA identifies the use of a DBX method that has an equivalence in Snowsight;
however, the element has a URL path as a parameter, which is not supported in Snowflake/Snowsight.

## Scenario

**Input**

The following example shows a `dbutils` method called with a URL path as a parameter:

```python
dbutils.fs.cp("s3://example.com/data.csv", "/mnt/data/")
```

**Output**

The SMA adds the EWI `SPRKDBX1005` on the output code to let you know that the method has URL as a parameter and is not supported.

```python
# EWI: SPRKDBX1005 => The method 'dbutils.fs.cp' has an equivalent in Snowflake/Snowsight; however, the element's parameter, a URL path, is not supported.
sfutils.fs.cp("s3://example.com/data.csv", "/mnt/data/")
```

**Recommended fix**

As this is a generic warning for functions with URL path parameters, there is no single recommended fix.
You should review the specific method and its usage to determine if an alternative approach or workaround is possible in
Snowflake/Snowsight.
Consider refactoring the code to avoid using URL paths, or implement custom logic to handle the data transfer outside the method.

Please note that even though the URL paths are not supported, it does not necessarily mean that a solution or workaround cannot be found. It only means that the SMA itself cannot find the solution.

Snowflake offers the ability to map URL-type paths by using an external stage, which facilitates the integration and access to external data.
Consult the Snowflake documentation on "[External Stage](https://docs.snowflake.com/en/user-guide/data-load-s3-create-stage)" for more information.

For this, you must have a storage integration ([STORAGE_INTEGRATION](https://docs.snowflake.com/en/sql-reference/sql/create-storage-integration)) configured.
Subsequently, you will need to copy your files to a table. You can find more details on how to copy data from an [S3 stage](https://docs.snowflake.com/en/user-guide/data-load-s3-copy) in the Snowflake documentation

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue). If you have a contract for support with Snowflake, reach out to your sales engineer, and they can direct your support needs.
