# Minimal CSN Generator - SAP BDC Compatible

Generate **minimal CSN Interop v1.0** from Snowflake (or other databases) that matches SAP BDC Connect requirements for maximum acceptance likelihood.

## When to Use This Skill

Use this skill when:
- Publishing Snowflake tables to SAP Datasphere/BDC via `SYSTEM$SAP_PUBLISH_DATA_PRODUCT`
- The user needs CSN that will be accepted by SAP BDC publishing APIs
- CSN generation from database schema (Snowflake, Postgres, BigQuery, etc.)
- Maximum compatibility with SAP BDC is more important than rich semantic metadata

**DO NOT use** when:
- User explicitly wants comprehensive annotations for SAP Datasphere consumption (use enhanced skill)
- Building CSN for CAP applications (use full CSN with associations)

## Critical Architecture Understanding (July 2026)

### The Zero-Copy Materialization Pipeline

```
Snowflake Iceberg (parquet) → SAP Materialization Job (≤10 min) → HDL Files (Delta Table) → CSN Validation
```

**Key insight:** SAP validates CSN against the **materialized Delta table**, which is created from the Snowflake **Iceberg** column types. This is why:
1. Type mappings must follow the Snowflake → Iceberg → CSN chain (not Snowflake's nominal types)
2. Empty tables fail ("Remote object not found") because no Delta table gets created
3. You must wait ~10 minutes after publishing before deploying on SAP

### Why INTEGER → cds.Integer

When a column is declared as an Iceberg `int` (32-bit) it maps to `cds.Integer`; an Iceberg `long` (64-bit) maps to `cds.Integer64`. Snowflake `INTEGER` produces Iceberg `int` and `BIGINT` produces Iceberg `long`. Only fixed-point `NUMBER(p,s)`/`DECIMAL(p,s)` (Iceberg `decimal(p,s)`) maps to `cds.Decimal(p,s)`.

### Why FLOAT → cds.Double

Snowflake `FLOAT`/`FLOAT4`/`FLOAT8` are 32-bit Iceberg `float`. Declare them as `cds.Double` in CSN — MSA applies `floatToDoubleWidening` automatically. `REAL`/`DOUBLE PRECISION` are Iceberg `double` (64-bit) and also map to `cds.Double`.

### Why strings have no length

`VARCHAR`/`STRING`/`TEXT` map to Iceberg `string` → `cds.String` with **no length**. Do NOT emit `cds.String(255)` or `length: 5000` — the length must be omitted entirely.

### Unsupported types

`TIME`, high-precision timestamps (`TIMESTAMP_*(9)` → Iceberg `timestamp_ns`), `BINARY`/`BINARY(n)`, `VARIANT`, `OBJECT`, `ARRAY`, `MAP`, `GEOMETRY`, and `GEOGRAPHY` are **not supported**. Exclude or convert these columns before publishing.

## What This Generates

**Minimal CSN v1.0** with:
- ✅ Core structure (`definitions`, `kind`, `elements`, `types`)
- ✅ Primary key designation (`key: true`)
- ✅ Foreign key associations (if FK constraints available)
- ✅ Correct type mapping (Snowflake → Iceberg → CDS types)
- ✅ `@ObjectModel.foreignKey.association` on FK columns (if FKs exist)
- ✅ `@PersonalData.*` annotations (when PII columns detected)
- ❌ NO display labels or i18n translations
- ❌ NO semantic metadata (@Semantics, @Aggregation, etc.)

## SAP BDC Valid CDS Types (Complete List)

Types produced by this mapping:
```
cds.Boolean, cds.Integer, cds.Integer64, cds.Decimal, cds.Double,
cds.String (no length), cds.Date, cds.Timestamp, cds.Association
```

**⚠️ DO NOT USE:**
- `cds.String(n)` / `length` on strings → emit `cds.String` with no length
- `cds.Time` → TIME is not supported (exclude the column)
- `cds.DateTime` → use `cds.Timestamp` for supported timestamps
- `cds.Binary` → BINARY is not supported (exclude the column)

## Type Mapping (Snowflake → Iceberg → CSN)

| Snowflake Type | Iceberg Type | CSN Type | Notes |
|----------------|--------------|----------|-------|
| BOOLEAN | boolean | `cds.Boolean` | |
| int | int | `cds.Integer` | Only when declared as Iceberg DDL `int` — produces Iceberg `int` (32-bit) |
| long | long | `cds.Integer64` | Only when declared as Iceberg DDL `long` — produces Iceberg `long` (64-bit) |
| INTEGER | int | `cds.Integer` | |
| BIGINT | long | `cds.Integer64` | |
| NUMBER(p,s) | decimal(p,s) | `cds.Decimal(p,s)` | Use matching precision and scale |
| DECIMAL(p,s) | decimal(p,s) | `cds.Decimal(p,s)` | Snowflake alias for `NUMBER(p,s)` |
| FLOAT | float | `cds.Double` | Iceberg type is `float` (32-bit). Declare as `cds.Double` — MSA applies `floatToDoubleWidening` automatically |
| FLOAT4 | float | `cds.Double` | Iceberg type is `float` (32-bit). Same widening as FLOAT |
| FLOAT8 | float | `cds.Double` | Iceberg type is `float` (32-bit). Same widening as FLOAT |
| REAL | double | `cds.Double` | Iceberg type is `double` (64-bit) |
| DOUBLE PRECISION | double | `cds.Double` | Iceberg type is `double` (64-bit) |
| VARCHAR | string | `cds.String` | Do **not** include a length (use `cds.String`, not `cds.String(255)`) |
| STRING | string | `cds.String` | Snowflake synonym for `VARCHAR(134217728)`. Do **not** include a length |
| TEXT | string | `cds.String` | Snowflake synonym for `VARCHAR(134217728)`. Do **not** include a length |
| DATE | date | `cds.Date` | |
| TIMESTAMP_NTZ(6) | timestamp | `cds.Timestamp` | |
| TIMESTAMP_LTZ(6) | timestamptz | `cds.Timestamp` | |
| TIMESTAMP_NTZ(9) | timestamp_ns | — | Not supported |
| TIMESTAMP_LTZ(9) | timestamptz_ns | — | Not supported |
| TIME(6) | time | — | Not supported |
| BINARY | binary | — | Not supported |
| BINARY(n) | fixed(n) | — | Not supported |
| VARIANT | variant | — | Not supported |
| OBJECT(...) | struct | — | Not supported in Datasphere |
| ARRAY(...) | list | — | Not supported in Datasphere |
| MAP(...) | map | — | Not supported in Datasphere |
| GEOMETRY | geometry | — | Not supported |
| GEOGRAPHY | geography | — | Not supported |

## CSN Structural Requirements

```json
{
  "csnInteropEffective": "1.0",
  "$version": "2.0",
  "i18n": {},
  "meta": {
    "creator": "Snowflake CSN Interop Generator - Minimal",
    "flavor": "inferred"
  },
  "definitions": {
    "PUBLIC": { "kind": "context" },
    "PUBLIC.TABLE_NAME": {
      "kind": "entity",
      "elements": { ... }
    }
  }
}
```

**Critical Rules:**
1. Context = SCHEMA name (e.g., `PUBLIC`), NOT database name
2. Entity names must EXACTLY match table names in the share
3. At least one `key: true` element per entity
4. All String columns → `cds.String` with **no length**
5. UPPERCASE identifiers matching Snowflake

## PII Detection (New - July 2026)

Detect and annotate PII columns:

**Data Subject ID Detection:**
- Column named `*_ID` where prefix is `CUSTOMER`, `USER`, `PERSON`, `EMPLOYEE`, `KUNNR`
- → Add `@PersonalData.fieldSemantics: {"#": "DataSubjectIDType"}`

**Potentially Personal Detection:**
- Column named `*EMAIL*`, `*PHONE*`, `*SSN*`, `*ADDRESS*`, `*NAME*` (except company names)
- → Add `@PersonalData.isPotentiallyPersonal: true`

**Entity-Level PII:**
- If entity has Data Subject ID column
- → Add `@PersonalData.entitySemantics: "DataSubjectDetails"` to entity

## Type Mapping Function (Python)

```python
def map_to_cds_type(source_type: str, precision: int = None, scale: int = None) -> dict:
    """Map a Snowflake type to a CDS type following the Snowflake -> Iceberg -> CSN chain.

    Raises ValueError for types SAP BDC / Datasphere cannot materialize.
    """
    upper_type = source_type.upper()

    # String types -> cds.String with NO length
    if any(t in upper_type for t in ['VARCHAR', 'STRING', 'TEXT', 'CHAR']):
        return {"type": "cds.String"}  # no length

    # 32-bit integer (Iceberg int)
    if upper_type in ['INTEGER', 'INT', 'SMALLINT', 'TINYINT', 'BYTEINT']:
        return {"type": "cds.Integer"}

    # 64-bit integer (Iceberg long)
    if upper_type == 'BIGINT':
        return {"type": "cds.Integer64"}

    # Fixed-point NUMBER/DECIMAL -> cds.Decimal(p,s) with exact precision/scale
    if upper_type in ['NUMBER', 'DECIMAL', 'NUMERIC']:
        effective_precision = precision if precision is not None else 38
        effective_scale = scale if scale is not None else 0
        return {
            "type": "cds.Decimal",
            "precision": effective_precision,
            "scale": effective_scale
        }

    # Floating point -> cds.Double (MSA widens float -> double automatically)
    if upper_type in ['FLOAT', 'FLOAT4', 'FLOAT8', 'REAL', 'DOUBLE', 'DOUBLE PRECISION']:
        return {"type": "cds.Double"}

    # Boolean
    if upper_type in ['BOOLEAN', 'BOOL']:
        return {"type": "cds.Boolean"}

    # Date
    if upper_type == 'DATE':
        return {"type": "cds.Date"}

    # Timestamp at microsecond precision -> cds.Timestamp.
    # Nanosecond precision (TIMESTAMP_*(9) -> Iceberg timestamp_ns) is NOT supported.
    if upper_type in ['TIMESTAMP', 'TIMESTAMP_NTZ', 'TIMESTAMP_LTZ', 'DATETIME']:
        if precision is not None and precision > 6:
            raise ValueError(
                f"{source_type}({precision}) not supported - nanosecond timestamps "
                "map to Iceberg timestamp_ns, which SAP BDC cannot materialize. "
                "Reduce to microsecond precision, e.g. TIMESTAMP_NTZ(6)."
            )
        return {"type": "cds.Timestamp"}

    # Unsupported types -> reject with a clear message
    unsupported = {
        'TIMESTAMP_TZ': 'TIMESTAMP_TZ is not supported',
        'TIME': 'TIME (Iceberg time) is not supported',
        'BINARY': 'BINARY/VARBINARY (Iceberg binary/fixed) is not supported',
        'VARIANT': 'VARIANT is not supported',
        'OBJECT': 'OBJECT (struct) is not supported in Datasphere',
        'ARRAY': 'ARRAY (list) is not supported in Datasphere',
        'MAP': 'MAP is not supported in Datasphere',
        'GEOMETRY': 'GEOMETRY is not supported',
        'GEOGRAPHY': 'GEOGRAPHY is not supported',
    }
    for key, msg in unsupported.items():
        if key in upper_type:
            raise ValueError(f"{source_type} not supported: {msg}. Exclude or convert this column.")

    raise ValueError(f"Unknown type '{source_type}' - no CSN mapping available")
```

## Pre-Publish Checklist

Before calling `SYSTEM$SAP_PUBLISH_DATA_PRODUCT`:

- [ ] **Tables have ≥1 row of data** (parquet files must exist for materialization)
- [ ] CSN entity names match `DESCRIBE SHARE` table names exactly
- [ ] INTEGER columns use `cds.Integer`; BIGINT columns use `cds.Integer64`
- [ ] NUMBER/DECIMAL columns use `cds.Decimal` with exact precision/scale from `INFORMATION_SCHEMA.COLUMNS`
- [ ] FLOAT/FLOAT4/FLOAT8/REAL/DOUBLE columns use `cds.Double`
- [ ] Supported TIMESTAMP columns (microsecond precision) use `cds.Timestamp`
- [ ] **No TIME, nanosecond timestamps, BINARY, VARIANT, OBJECT, ARRAY, MAP, GEOMETRY, or GEOGRAPHY columns** (unsupported)
- [ ] Context is SCHEMA name (e.g., `PUBLIC`), not database name
- [ ] All String columns use `cds.String` with **no length**

After publishing:

- [ ] **Wait ≥10 minutes** for SAP materialization job to complete before deploying

## Workflow

### Step 1: Validate Tables Have Data

```sql
-- Check row counts before publishing
SELECT TABLE_NAME, ROW_COUNT 
FROM INFORMATION_SCHEMA.TABLES 
WHERE TABLE_SCHEMA = 'PUBLIC' 
  AND TABLE_NAME IN ('TABLE1', 'TABLE2');

-- Tables with 0 rows will cause "Remote object not found" errors
```

### Step 2: Extract Metadata with Exact Precision

```sql
-- Get columns WITH exact precision (critical for DECIMAL mapping)
SELECT 
    TABLE_NAME,
    COLUMN_NAME,
    DATA_TYPE,
    NUMERIC_PRECISION,  -- Use this exact value for cds.Decimal
    NUMERIC_SCALE,      -- Use this exact value for cds.Decimal
    IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'PUBLIC'
  AND TABLE_NAME IN ('TABLE1', 'TABLE2')
ORDER BY TABLE_NAME, ORDINAL_POSITION;
```

### Step 3: Generate CSN

Use the type mapping function above. Key points:
- INTEGER → `cds.Integer`
- BIGINT → `cds.Integer64`
- NUMBER(p,s)/DECIMAL(p,s) → `cds.Decimal(p,s)` (exact precision/scale)
- FLOAT/FLOAT4/FLOAT8/REAL/DOUBLE → `cds.Double`
- VARCHAR/STRING/TEXT → `cds.String` (no length)
- TIMESTAMP_NTZ(6)/LTZ(6) → `cds.Timestamp`
- Reject TIME, nanosecond timestamps, BINARY, VARIANT, OBJECT, ARRAY, MAP, GEOMETRY, GEOGRAPHY columns with an error

### Step 4: Verify Entity Names Match Share

```sql
-- After publishing, verify CSN entity names match share
DESCRIBE SHARE my_share;
-- Compare table names against CSN definitions
```

### Step 5: Wait for Materialization

```
Publishing complete. 
⏳ Wait 10 minutes for SAP materialization job before deploying.
```

## Example CSN Output

```json
{
  "csnInteropEffective": "1.0",
  "$version": "2.0",
  "i18n": {},
  "meta": {
    "creator": "Snowflake CSN Interop Generator - Minimal",
    "flavor": "inferred"
  },
  "definitions": {
    "PUBLIC": { "kind": "context" },
    "PUBLIC.CUSTOMERS": {
      "kind": "entity",
      "@PersonalData.entitySemantics": "DataSubjectDetails",
      "elements": {
        "CUSTOMER_ID": {
          "type": "cds.String",
          "key": true,
          "notNull": true,
          "@PersonalData.fieldSemantics": {"#": "DataSubjectIDType"}
        },
        "EMAIL": {
          "type": "cds.String",
          "@PersonalData.isPotentiallyPersonal": true
        },
        "ORDER_COUNT": {
          "type": "cds.Integer"
        },
        "TOTAL_AMOUNT": {
          "type": "cds.Decimal",
          "precision": 15,
          "scale": 2
        },
        "CREATED_AT": {
          "type": "cds.Timestamp"
        }
      }
    }
  }
}
```

## Troubleshooting

### Error: "Remote object '{0}' could not be found"

**Causes (in order of likelihood):**
1. **Table has 0 rows** → Add data before publishing
2. **Materialization not complete** → Wait 10 minutes after publishing
3. **Context is DATABASE name** → Use SCHEMA name (e.g., `PUBLIC`)
4. **Entity name mismatch** → Run `DESCRIBE SHARE` and verify names match

### Error: type mismatch on an integer column

**Cause:** Emitting `cds.Decimal` (or a length) where the Iceberg type is `int`/`long`  
**Fix:** Use `cds.Integer` for INTEGER and `cds.Integer64` for BIGINT; reserve `cds.Decimal(p,s)` for NUMBER/DECIMAL columns only

### Error: type mismatch on a string column

**Cause:** Emitting a `length` on `cds.String` (e.g. `cds.String(5000)`)  
**Fix:** Emit `cds.String` with **no length** for VARCHAR/STRING/TEXT columns

### Error: "MISMATCH IN DATA TYPE PRECISION"

**Cause:** Using assumed precision (38) instead of actual  
**Fix:** Query `INFORMATION_SCHEMA.COLUMNS` for exact `NUMERIC_PRECISION`/`NUMERIC_SCALE`

### Error: materialization fails for TIME / BINARY / VARIANT / nanosecond timestamp

**Cause:** These Iceberg types (`time`, `binary`/`fixed`, `variant`, `timestamp_ns`) cannot be materialized by SAP  
**Fix:** Exclude the column, or convert it (e.g. cast to a supported type, reduce timestamp precision to `(6)`)

## Annotations Included

| Annotation | Included | Condition | Test Evidence |
|------------|----------|-----------|---------------|
| `@ObjectModel.foreignKey.association` | ✅ | If FK constraints exist | sap_exp_assoc_01 |
| `@PersonalData.fieldSemantics` | ✅ | Data subject ID columns | sap_exp_pii_01 |
| `@PersonalData.entitySemantics` | ✅ | Entity has PII columns | sap_exp_pii_02 |
| `@PersonalData.isPotentiallyPersonal` | ✅ | Potentially personal columns | sap_exp_pii_03 |

## Success Criteria

✅ **Primary Goal:** SAP BDC accepts minimal CSN and deployment succeeds

**Validation:**
1. CSN publishes successfully via `SYSTEM$SAP_PUBLISH_DATA_PRODUCT`
2. Wait 10 minutes for materialization
3. Data product deploys successfully in SAP Datasphere
4. No type mismatch errors during deployment

---

*Minimal CSN: Maximum compatibility, correct type mappings, essential PII annotations.*
