# Snowflake to CDS Type Mapping

## Complete Mapping Table

| Snowflake Type | CDS Type | Arguments | Notes |
|---|---|---|---|
| VARCHAR(n) | cds.String / cds.LargeString | length: n (if ≤ 5000) | Use cds.LargeString (omit length) when n > 5000 — CSN spec caps cds.String.length at 5000 |
| CHAR(n) | cds.String / cds.LargeString | length: n (if ≤ 5000) | Use cds.LargeString (omit length) when n > 5000 — CSN spec caps cds.String.length at 5000 |
| STRING | cds.LargeString | - | Snowflake default 16777216 exceeds CSN cap of 5000 — use cds.LargeString unless metadata shows a smaller explicit length |
| TEXT | cds.LargeString | - | Snowflake default 16777216 exceeds CSN cap of 5000 — use cds.LargeString unless metadata shows a smaller explicit length |
| NUMBER(p,s) | cds.Decimal | precision: p, scale: s | Snowflake default: NUMBER(38,0) |
| DECIMAL(p,s) | cds.Decimal | precision: p, scale: s | Alias for NUMBER |
| NUMERIC(p,s) | cds.Decimal | precision: p, scale: s | Alias for NUMBER |
| INT / INTEGER | cds.Decimal | precision: 38, scale: 0 | **Snowflake stores as NUMBER(38,0)** — see critical note below |
| BIGINT | cds.Decimal | precision: 38, scale: 0 | **Snowflake stores as NUMBER(38,0)** — see critical note below |
| SMALLINT | cds.Decimal | precision: 38, scale: 0 | **Snowflake stores as NUMBER(38,0)** — see critical note below |
| TINYINT | cds.Decimal | precision: 38, scale: 0 | **Snowflake stores as NUMBER(38,0)** — see critical note below |
| BYTEINT | cds.Decimal | precision: 38, scale: 0 | **Snowflake stores as NUMBER(38,0)** — see critical note below |
| FLOAT | cds.Double | - | IEEE 754 double precision |
| FLOAT4 | cds.Double | - | Alias |
| FLOAT8 | cds.Double | - | Alias |
| DOUBLE | cds.Double | - | IEEE 754 double precision |
| DOUBLE PRECISION | cds.Double | - | Alias |
| REAL | cds.Double | - | Alias |
| BOOLEAN | cds.Boolean | - | |
| DATE | cds.Date | - | |
| TIME | cds.Time | - | |
| TIMESTAMP_NTZ | cds.Timestamp | - | No timezone; preferred for interop |
| TIMESTAMP_LTZ | cds.Timestamp | - | Local timezone; converted on read |
| TIMESTAMP_TZ | cds.Timestamp | - | Stored with timezone offset |
| DATETIME | cds.Timestamp | - | Alias for TIMESTAMP_NTZ |
| BINARY(n) | cds.Binary | length: n | |
| VARBINARY(n) | cds.Binary | length: n | |
| VARIANT | cds.LargeString | - | JSON serialized; no CDS structured type |
| OBJECT | cds.LargeString | - | JSON serialized; no CDS structured type |
| ARRAY | cds.LargeString | - | JSON serialized; CSN doesn't support arrays |
| GEOGRAPHY | cds.LargeString | - | GeoJSON serialized; no CDS spatial type |
| GEOMETRY | cds.LargeString | - | GeoJSON serialized; no CDS spatial type |
| VECTOR | cds.LargeString | - | No CDS vector type |

## Critical: INTEGER Types in Snowflake

> **IMPORTANT FOR SAP BDC PUBLISHING**: Snowflake stores ALL integer types (INT, INTEGER, BIGINT, SMALLINT, TINYINT, BYTEINT) internally as `NUMBER(38,0)`. When SAP Datasphere reads the remote table through the zero-copy connector, it sees `DECIMAL` — NOT `INTEGER`.
>
> **If your CSN says `cds.Integer` but Datasphere sees `DECIMAL` from the remote table, the import WILL FAIL with:**
> ```
> MISMATCH IN DATA TYPE: inputDataType "INTEGER", actualDataType "DECIMAL"
> ```
>
> **ALWAYS map Snowflake numeric columns to `cds.Decimal`** with the appropriate precision and scale from INFORMATION_SCHEMA:
> - `NUMBER(10,0)` → `{"type": "cds.Decimal", "precision": 10, "scale": 0}`
> - `NUMBER(38,0)` (INT/BIGINT) → `{"type": "cds.Decimal", "precision": 38, "scale": 0}`
> - `NUMBER(16,2)` → `{"type": "cds.Decimal", "precision": 16, "scale": 2}`
>
> **NEVER use `cds.Integer` or `cds.Integer64`** for Snowflake columns when publishing via BDC Connect. These CDS types do not match what Datasphere sees from the remote table.

## Design Decisions

1. **INTEGER variants**: Snowflake stores ALL integer types as NUMBER(38,0). For SAP BDC publishing, these MUST be mapped to `cds.Decimal` with `precision: 38, scale: 0` to match what SAP Datasphere sees from the remote table. Using `cds.Integer` causes type mismatch errors during import.

2. **TIMESTAMP variants**: All three Snowflake timestamp types map to `cds.Timestamp`. CSN has no timezone-aware type. Consumers should be aware that TIMESTAMP_TZ data includes timezone offsets that may be lost in CSN representation.

3. **Semi-structured types**: VARIANT/OBJECT/ARRAY are mapped to `cds.LargeString` because CSN Interop Effective does not support structured or arrayed element types (by design, for interoperability across relational systems). Data should be serialized as JSON strings.

4. **Missing CDS types**: Snowflake GEOGRAPHY/GEOMETRY/VECTOR have no CDS equivalents. They are mapped to `cds.LargeString` with a `doc` annotation explaining the original type.