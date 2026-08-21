# SPRKSCL1161

Snowpark and Snowpark Extensions were not added to the project configuration file.

Message: Failed to add dependencies.

Category: Conversion error.

## Description

This issue occurs when the SMA detects a Spark version in the project configuration file that is not supported by the SMA, therefore  SMA could not add the Snowpark and Snowpark Extensions dependencies to the corresponding project configuration file. If Snowpark dependencies are not added, the migrated code will not compile.

## Scenarios

There are three possible scenarios: sbt, gradle and pom.xml.
The SMA tries to process the project configuration file by removing Spark dependencies and adding Snowpark and Snowpark Extensions dependencies.

### Scenario 1

**Input**

Below is an example of the `dependencies` section of a `sbt` project configuration file.

```sbt
...
libraryDependencies += "org.apache.spark" % "spark-core_2.13" % "3.5.3"
libraryDependencies += "org.apache.spark" % "spark-sql_2.13" % "3.5.3"
...
```

**Output**

The SMA adds the EWI `SPRKSCL1161` to the issues inventory since the Spark version is not supported and keeps the output the same.

```sbt
...
libraryDependencies += "org.apache.spark" % "spark-core_2.13" % "3.5.3"
libraryDependencies += "org.apache.spark" % "spark-sql_2.13" % "3.5.3"
...
```

**Recommended fix**

Manually, remove the Spark dependencies and add Snowpark and Snowpark Extensions dependencies to the `sbt` project configuration file.

```sbt
...
libraryDependencies += "com.snowflake" % "snowpark" % "1.14.0"
libraryDependencies += "net.mobilize.snowpark-extensions" % "snowparkextensions" % "0.0.18"
...
```

Make sure to use the Snowpark version that best meets your project's requirements.

### Scenario 2

**Input**

Below is an example of the `dependencies` section of a `gradle` project configuration file.

```gradle
dependencies {
    implementation group: 'org.apache.spark', name: 'spark-core_2.13', version: '3.5.3'
    implementation group: 'org.apache.spark', name: 'spark-sql_2.13', version: '3.5.3'
    ...
}
```

**Output**

The SMA adds the EWI `SPRKSCL1161` to the issues inventory since the Spark version is not supported and keeps the output the same.

```gradle
dependencies {
    implementation group: 'org.apache.spark', name: 'spark-core_2.13', version: '3.5.3'
    implementation group: 'org.apache.spark', name: 'spark-sql_2.13', version: '3.5.3'
    ...
}
```

**Recommended fix**

Manually, remove the Spark dependencies and add Snowpark and Snowpark Extensions dependencies to the `gradle` project configuration file.

```gradle
dependencies {
    implementation 'com.snowflake:snowpark:1.14.2'
    implementation 'net.mobilize.snowpark-extensions:snowparkextensions:0.0.18'
    ...
}
```

Make sure that dependencies version are according to your project needs.

### Scenario 3

**Input**

Below is an example of the `dependencies` section of a `pom.xml` project configuration file.

```xml
<dependencies>
  <dependency>
    <groupId>org.apache.spark</groupId>
    <artifactId>spark-core_2.13</artifactId>
    <version>3.5.3</version>
  </dependency>

  <dependency>
    <groupId>org.apache.spark</groupId>
    <artifactId>spark-sql_2.13</artifactId>
    <version>3.5.3</version>
    <scope>compile</scope>
  </dependency>
  ...
</dependencies>
```

**Output**

The SMA adds the EWI `SPRKSCL1161` to the issues inventory since the Spark version is not supported and keeps the output the same.

```xml
<dependencies>
  <dependency>
    <groupId>org.apache.spark</groupId>
    <artifactId>spark-core_2.13</artifactId>
    <version>3.5.3</version>
  </dependency>

  <dependency>
    <groupId>org.apache.spark</groupId>
    <artifactId>spark-sql_2.13</artifactId>
    <version>3.5.3</version>
    <scope>compile</scope>
  </dependency>
  ...
</dependencies>
```

**Recommended fix**

Manually, remove the Spark dependencies and add Snowpark and Snowpark Extensions dependencies to the `gradle` project configuration file.

```xml
<dependencies>
  <dependency>
    <groupId>com.snowflake</groupId>
    <artifactId>snowpark</artifactId>
    <version>1.14.2</version>
  </dependency>

  <dependency>
    <groupId>net.mobilize.snowpark-extensions</groupId>
    <artifactId>snowparkextensions</artifactId>
    <version>0.0.18</version>
  </dependency>
  ...
</dependencies>
```

Make sure that dependencies version are according to your project needs.


## Additional recommendations

- Make sure that input has a project configuration file:
  - build.sbt
  - build.gradle
  - pom.xml


- Spark version supported by the SMA is 2.12:3.1.2


- You can check the latest Snowpark version [here](https://github.com/snowflakedb/snowpark-java-scala/releases/latest).


- For more support, you can email us at [sma-support@snowflake.com](mailto:sma-support@snowflake.com) or post an issue [in the SMA](https://docs.snowconvert.com/sma/user-guide/project-overview/configuration-and-settings#report-an-issue).
