# SPRKPY1103

pyspark.sql.functions.xpath*

**Message**: The pyspark.sql.functions.xpath* function family has a workaround

**Category**: Warning

---
## Description

This issue appears when the tool detects the usage of the [`pyspark.sql.functions.xpath*`](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.xpath.html) function family, which requires a workaround to work correctly in Snowpark.

The fundamental difference is that the `xpath` functions in Snowpark require the input XML string to have a **single root element** (e.g., `<root>...</root>`), whereas PySpark can be more flexible and process XML fragments that are not wrapped in a single root.

The equivalent in Snowpark is the [`snowflake.snowpark.functions.xpath*`](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.xpath) function family, but the restriction on the XML format must be taken into account to ensure compatibility.

---
## Scenarios

### Scenario 1: Extracting numerical data from XML without a single root element

**Input**

The original PySpark code processes a column with XML transaction records. Each string contains multiple root-level elements, such as `<id>`, `<items>`, and `<amount>`.

```python
# Original PySpark code
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, xpath_string, xpath, lit

spark = SparkSession.builder.appName("PySparkXMLExample").getOrCreate()

generic_data = [
  (
    """
    <item id="XYZ-001">
      <details>
        <category>A</category>
        <timestamp>1677614400</timestamp>
      </details>
      <attributes>
        <attribute key="size">Large</attribute>
        <attribute key="color">Blue</attribute>
      </attributes>
    </item>
    """
    ,),
]

generic_df = spark.createDataFrame(generic_data, ["ITEM_XML"])

structured_items = generic_df.select(
  xpath_string(col("ITEM_XML"), lit("item/@id")).alias("ITEM_ID"),
  xpath_string(col("ITEM_XML"), lit("item/details/category/text()")).alias("CATEGORY"),
  xpath(col("ITEM_XML"), lit("item/attributes/attribute/text()")).alias("ATTRIBUTES")
)
structured_items.show()
```

### Output

The tool migrates the code but adds the `EWI SPRKPY1103` warning. This flag indicates that manual adjustments are needed because the code will fail in Snowpark due to differences in XML handling.

```python
# Migrated code with a warning
from snowflake.snowpark import Session
from snowflake.snowpark.functions import col, xpath_string, xpath, lit

spark = Session.builder.app_name("PySparkXMLExample", True).getOrCreate()

generic_data = [
    (
        """
        <item id="XYZ-001">
          <details>
            <category>A</category>
            <timestamp>1677614400</timestamp>
          </details>
          <attributes>
            <attribute key="size">Large</attribute>
            <attribute key="color">Blue</attribute>
          </attributes>
        </item>
        """
        ,),
]

generic_df = spark.createDataFrame(generic_data, ["ITEM_XML"])
#EWI: SPRKPY1103 => The pyspark.sql.functions.xpath* function family requires a workaround because their Snowpark equivalents expect the XML string to be enclosed in a single root element

structured_items = generic_df.select(
    xpath_string(col("ITEM_XML"), lit("item/@id")).alias("ITEM_ID"),
    xpath_string(col("ITEM_XML"), lit("item/details/category/text()")).alias("CATEGORY"),
    xpath(col("ITEM_XML"), lit("item/attributes/attribute/text()")).alias("ATTRIBUTES")
)
structured_items.show()
```

### Recommended fix

The migrated code can fail in Snowpark due to a difference in how XML is parsed. Errors can occur when processing XML strings that lack a single root element. The recommended fix is to transform each XML fragment into a valid document by enclosing it within a single root tag (e.g., `<root>`).

For example, an XML string like `<data>...</data><info>...</info>` must be modified to `<root><data>...</data><info>...</info></root>`. 

You do not need to change your original XPath expressions.


```python
# Corrected and functional Snowpark code
from snowflake.snowpark import Session
from snowflake.snowpark.functions import col, xpath_string, xpath, lit

spark = Session.builder.app_name("PySparkXMLExample", True).getOrCreate()

generic_data = [
    (
        """
        <root>
            <item id="XYZ-001">
              <details>
                <category>A</category>
                <timestamp>1677614400</timestamp>
              </details>
              <attributes>
                <attribute key="size">Large</attribute>
                <attribute key="color">Blue</attribute>
              </attributes>
            </item>
        </root>
        """
        ,),
]

generic_df = spark.createDataFrame(generic_data, ["ITEM_XML"])
#EWI: SPRKPY1103 => The pyspark.sql.functions.xpath* function family requires a workaround because their Snowpark equivalents expect the XML string to be enclosed in a single root element

structured_items = generic_df.select(
    xpath_string(col("ITEM_XML"), lit("item/@id")).alias("ITEM_ID"),
    xpath_string(col("ITEM_XML"), lit("item/details/category/text()")).alias("CATEGORY"),
    xpath(col("ITEM_XML"), lit("item/attributes/attribute/text()")).alias("ATTRIBUTES")
)
structured_items.show()
```

### Expected result

After applying the fix, the nested and attribute data is extracted correctly into the structured DataFrame:

```text
-----------------------------------------  
|"ITEM_ID"  |"CATEGORY"  |"ATTRIBUTES"  |  
-----------------------------------------  
|XYZ-001    |A           |[             |  
|           |            |  "Large",    |  
|           |            |  "Blue"      |  
|           |            |]             |  
-----------------------------------------
```

## Additional recommendations

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).