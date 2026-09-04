# OpenLineage Namespace Naming Conventions

Datasets and jobs have their own namespaces. Dataset namespaces are derived from datasources, job namespaces from schedulers.

## Dataset Namespace & Name Format

| Source | Type | Namespace Format | Name Format |
|--------|------|------------------|-------------|
| **Snowflake** | Warehouse | `snowflake://{org name}-{account name}` or `snowflake://{account-locator}(.{region})(.{cloud})` | `{database}.{schema}.{table}` |
| **Postgres** | Warehouse | `postgres://{host}:{port}` | `{database}.{schema}.{table}` |
| **MySQL** | Warehouse | `mysql://{host}:{port}` | `{database}.{table}` |
| **MSSQL** | Warehouse | `mssql://{host}:{port}` | `{database}.{schema}.{table}` |
| **Oracle** | Warehouse | `oracle://{host}:{port}` | `{serviceName}.{schema}.{table}` |
| **BigQuery** | Warehouse | `bigquery` | `{project id}.{dataset name}.{table name}` |
| **Redshift** | Warehouse | `redshift://{cluster_identifier}.{region_name}:{port}` | `{database}.{schema}.{table}` |
| **Athena** | Warehouse | `awsathena://athena.{region_name}.amazonaws.com` | `{catalog}.{database}.{table}` |
| **AWS Glue** | Data catalog | `arn:aws:glue:{region}:{account id}` | `table/{database name}/{table name}` |
| **Hive** | Warehouse | `hive://{host}:{port}` | `{database}.{table}` |
| **Trino** | Warehouse | `trino://{host}:{port}` | `{catalog}.{schema}.{table}` |
| **Cassandra** | Warehouse | `cassandra://{host}:{port}` | `{keyspace}.{table}` |
| **DB2** | Warehouse | `db2://{host}:{port}` | `{database}.{schema}.{table}` |
| **Teradata** | Warehouse | `teradata://{host}:{port}` | `{database}.{table}` |
| **Azure Synapse** | Warehouse | `sqlserver://{host}:{port}` | `{schema}.{table}` |
| **Azure Cosmos DB** | Warehouse | `azurecosmos://{host}/dbs/{database}` | `colls/{table}` |
| **Azure Data Explorer** | Warehouse | `azurekusto://{host}.kusto.windows.net` | `{database}/{table}` |
| **Spanner** | Warehouse | `spanner://{projectId}:{instanceId}` | `{database}.{schema}.{table}` |
| **S3** | Blob Storage | `s3://{bucket name}` | `{object key}` |
| **GCS** | Blob Storage | `gs://{bucket name}` | `{object key}` |
| **ABFSS** | Data Lake | `abfss://{container}@{service}.dfs.core.windows.net` | `{path}` |
| **WASBS** | Blob Storage | `wasbs://{container}@{service}.dfs.core.windows.net` | `{object key}` |
| **HDFS** | Distributed FS | `hdfs://{namenode host}:{namenode port}` | `{path}` |
| **DBFS** | Distributed FS | `dbfs://{workspace name}` | `{path}` |
| **Kafka** | Event Streaming | `kafka://{bootstrap server host}:{port}` | `{topic}` |
| **PubSub** | Event Streaming | `pubsub` | `topic:{projectId}:{topicId}` or `subscription:{projectId}:{subscriptionId}` |
| **Local File** | File System | `file` | `{path}` |
| **Remote File** | File System | `file://{host}` | `{path}` |

## Snowflake Namespace - Important Notes

Snowflake has two namespace formats:
1. **Preferred:** `snowflake://{org name}-{account name}` (e.g., `snowflake://MYORG-MYACCOUNT`)
2. **Legacy:** `snowflake://{account-locator}.{region}.{cloud}` (e.g., `snowflake://xy12345.us-east-1.aws`)

**Warning:** Using legacy account locator format creates dataset IDs that won't match IDs created with the org-account format. If you switch formats later, existing lineage nodes won't connect to new ones. Use the org-account format when possible.

## Job Namespace & Name Format

| Scheduler | Name Format | Example |
|-----------|-------------|---------|
| Airflow task | `{dag_id}.{task_id}` | `orders_etl.count_orders` |
| Spark job | `{appName}.{command}.{table}` | `my_app.execute_insert_into_hive_table.mydb_mytable` |
| SQL | `{schema}.{table}` | `gx.validate_datasets` |
| Debezium | `{topic.prefix}.{taskId}` | `inventory.0` |

## Run ID Format
Runs use client-generated UUIDs (e.g., `f47ac10b-58cc-4372-a567-0e02b2c3d479`)
