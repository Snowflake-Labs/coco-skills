# SPRKPY1019

pyspark.sql.functions.datediff

> This Issue Code has been **deprecated** since [Spark Conversion Core Version 4.8.0 ](https://docs.snowconvert.com/sma/general/release-notes#spark-conversion-core-version-4.8.0)

Message: pyspark.sql.functions.datediff has a workaround

Category: Warning.

## Description

This issue appears when the tool detects the usage of [pyspark.sql.functions.datediff](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.datediff.html) which has a workaround.

## Scenario

**Input**

In this example we use datediff to calculate the diference in day from 'today' and others dates.
```python
contacts = (contacts
            #days since last event
            .withColumn('daysSinceLastEvent', datediff(lit(today),'lastEvent'))
            #days since deployment
            .withColumn('daysSinceLastDeployment', datediff(lit(today),'lastDeploymentEnd'))
            #days since online training
            .withColumn('daysSinceLastTraining', datediff(lit(today),'lastTraining'))
            #days since last RC login
            .withColumn('daysSinceLastRollCallLogin', datediff(lit(today),'adx_identity_lastsuccessfullogin'))
            #days since last EMS login
            .withColumn('daysSinceLastEMSLogin', datediff(lit(today),'vms_lastuserlogin'))
           )
```

**Output**

SMA returns the EWI SPRKPY1019 over the line where datediff is used, so you can use to identify where to fix.
```python
from pyspark.sql.functions import datediff
#EWI: SPRKPY1019 => pyspark.sql.functions.datediff has a workaround, see documentation for more info
contacts = (contacts
            #days since last event
            .withColumn('daysSinceLastEvent', datediff(lit(today),'lastEvent'))
            #days since deployment
            .withColumn('daysSinceLastDeployment', datediff(lit(today),'lastDeploymentEnd'))
            #days since online training
            .withColumn('daysSinceLastTraining', datediff(lit(today),'lastTraining'))
            #days since last RC login
            .withColumn('daysSinceLastRollCallLogin', datediff(lit(today),'adx_identity_lastsuccessfullogin'))
            #days since last EMS login
            .withColumn('daysSinceLastEMSLogin', datediff(lit(today),'vms_lastuserlogin'))
           )
```
SMA convert pyspark.sql.functions.datediff onto [snowflake.snowpark.functions.daydiff](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.daydiff) that also  calculates the diference in days between two dates.

**Recommended fix**

**datediff(part: string ,end: ColumnOrName, start: ColumnOrName)**

**Action:** Import snowflake.snowpark.functions, which contains an implementation for [**datediff**](https://docs.snowflake.com/en/developer-guide/snowpark/reference/python/latest/snowpark/api/snowflake.snowpark.functions.daydiff) function that requires an extra parameter for [date time part](https://docs.snowflake.com/en/sql-reference/functions-date-time#label-supported-date-time-parts) and allows more versatility on calculate differences between dates.

```python
from snowflake.snowpark import Session
from snowflake.snowpark.functions import datediff
contacts = (contacts
            #days since last event
            .withColumn('daysSinceLastEvent', datediff('day', lit(today),'lastEvent'))
            #days since deployment
            .withColumn('daysSinceLastDeployment', datediff('day',lit(today),'lastDeploymentEnd'))
            #days since online training
            .withColumn('daysSinceLastTraining', datediff('day', lit(today),'lastTraining'))
            #days since last RC login
            .withColumn('daysSinceLastRollCallLogin', datediff('day', lit(today),'adx_identity_lastsuccessfullogin'))
            #days since last EMS login
            .withColumn('daysSinceLastEMSLogin', datediff('day', lit(today),'vms_lastuserlogin'))
           )
```

## Recommendation

- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
