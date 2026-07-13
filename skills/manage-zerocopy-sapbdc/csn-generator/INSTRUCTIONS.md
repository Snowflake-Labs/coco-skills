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

**Key insight:** SAP validates CSN against the **materialized Delta table**, NOT directly against Iceberg. This is why:
1. Type mappings must match what the Delta table reports (not Snowflake's nominal types)
2. Empty tables fail ("Remote object not found") because no Delta table gets created
3. You must wait ~10 minutes after publishing before deploying on SAP

### Why INTEGER → cds.Decimal (Not cds.Integer)

Snowflake stores INTEGER/BIGINT as `decimal(38,0)` in Iceberg metadata. When SAP materializes:
- Iceberg `decimal(38,0)` → Delta table `DECIMAL(38,0)`
- CSN `cds.Integer` fails: "inputDataType=INTEGER, actualDataType=DECIMAL"
- CSN `cds.Decimal(38,0)` matches → ✅ Success

### Why TIME → cds.Timestamp (Not cds.Time)

Snowflake TIME in Iceberg metadata is `time`. When SAP materializes:
- Iceberg `time` → Delta table `TIMESTAMP`
- CSN `cds.Time` fails: "inputDataType=TIME, actualDataType=TIMESTAMP"
- CSN `cds.Timestamp` matches → ✅ Success

## What This Generates

**Minimal CSN v1.0** with:
- ✅ Core structure (`definitions`, `kind`, `elements`, `types`)
- ✅ Primary key designation (`key: true`)
- ✅ Foreign key associations (if FK constraints available)
- ✅ Correct type mapping (Snowflake → Iceberg → Delta → CDS types)
- ✅ `@ObjectModel.foreignKey.association` on FK columns (if FKs exist)
- ✅ `@PersonalData.*` annotations (when PII columns detected)
- ❌ NO display labels or i18n translations
- ❌ NO semantic metadata (@Semantics, @Aggregation, etc.)

## SAP BDC Valid CDS Types (Complete List)

From SAP BDC REST API validation:
```
cds.Boolean, cds.Decimal, cds.Double, cds.String, cds.LargeString,
cds.Date, cds.Timestamp, cds.UUID, cds.Association, cds.Composition
```

**⚠️ DO NOT USE (pass publish but fail deployment):**
- `cds.Integer` / `cds.Integer64` → use `cds.Decimal(p,0)` instead
- `cds.Time` → use `cds.Timestamp` instead
- `cds.DateTime` → use `cds.Timestamp` instead

## Type Mapping (Final - July 2026)

| Snowflake Type | Iceberg Metadata | Delta Table | CDS Type | Status |
|----------------|------------------|-------------|----------|--------|
| BOOLEAN | boolean | BOOLEAN | `cds.Boolean` | ✅ Validated |
| INTEGER | decimal(38,0) | DECIMAL(38,0) | `cds.Decimal(38,0)` | ✅ Validated |
| BIGINT | decimal(38,0) | DECIMAL(38,0) | `cds.Decimal(38,0)` | ✅ Validated |
| NUMBER(p,s) | decimal(p,s) | DECIMAL(p,s) | `cds.Decimal(p,s)` | ✅ Validated |
| DECIMAL(p,s) | decimal(p,s) | DECIMAL(p,s) | `cds.Decimal(p,s)` | ✅ Validated |
| FLOAT/FLOAT4/FLOAT8 | float | DOUBLE | `cds.Double` | ✅ Validated |
| REAL/DOUBLE | double | DOUBLE | `cds.Double` | ✅ Validated |
| VARCHAR/STRING/TEXT | string | NVARCHAR | `cds.String(5000)` | ✅ Validated |
| DATE | date | DATE | `cds.Date` | ✅ Validated |
| TIME | time | TIMESTAMP | `cds.Timestamp` | ✅ Validated |
| TIMESTAMP_NTZ | timestamp | TIMESTAMP | `cds.Timestamp` | ✅ Validated |
| TIMESTAMP_LTZ | timestamptz | TIMESTAMP | `cds.Timestamp` | ✅ Validated |
| VARIANT | string | NVARCHAR | `cds.String(5000)` | ✅ Validated |
| BINARY | binary | ❌ FAILS | N/A | ⛔ Unsupported |
| VARBINARY | N/A | N/A | N/A | ⛔ Blocked (Snowflake Iceberg doesn't support) |

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
4. All String columns → `length: 5000`
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
def map_to_cds_type(source_type: str, precision: int, scale: int) -> dict:
    """Map database type to CDS type following SAP materialization rules.
    
    CRITICAL: Types must match what SAP's Delta table reports, NOT Snowflake's nominal types.
    """
    upper_type = source_type.upper()
    
    # String types - ALWAYS use 5000 for remote mode compatibility
    if any(t in upper_type for t in ['VARCHAR', 'STRING', 'TEXT', 'CHAR']):
        return {"type": "cds.String", "length": 5000}
    
    # Integer types - MUST use cds.Decimal (SAP Delta reports DECIMAL)
    # DO NOT use cds.Integer - it fails at deployment
    if upper_type in ['INTEGER', 'INT', 'SMALLINT', 'TINYINT']:
        return {"type": "cds.Decimal", "precision": 38, "scale": 0}
    if upper_type == 'BIGINT':
        return {"type": "cds.Decimal", "precision": 38, "scale": 0}
    
    # Decimal/Number types - use exact precision from INFORMATION_SCHEMA
    if upper_type in ['DECIMAL', 'NUMERIC', 'NUMBER']:
        effective_precision = precision if precision is not None else 38
        effective_scale = scale if scale is not None else 0
        return {
            "type": "cds.Decimal",
            "precision": effective_precision,
            "scale": effective_scale
        }
    
    # Float types
    if upper_type in ['FLOAT', 'FLOAT4', 'FLOAT8', 'REAL', 'DOUBLE']:
        return {"type": "cds.Double"}
    
    # Boolean
    if upper_type in ['BOOLEAN', 'BOOL']:
        return {"type": "cds.Boolean"}
    
    # Date
    if upper_type == 'DATE':
        return {"type": "cds.Date"}
    
    # Time and Timestamp - ALL map to cds.Timestamp
    # DO NOT use cds.Time or cds.DateTime - they fail at deployment
    if upper_type == 'TIME':
        return {"type": "cds.Timestamp"}  # NOT cds.Time
    if upper_type in ['TIMESTAMP', 'TIMESTAMP_NTZ', 'TIMESTAMP_LTZ', 'DATETIME']:
        return {"type": "cds.Timestamp"}  # NOT cds.DateTime
    
    # VARIANT (JSON) - map to String, NOT LargeString
    # LargeString causes NCLOB vs NVARCHAR mismatch
    if upper_type == 'VARIANT':
        return {"type": "cds.String", "length": 5000}
    
    # Binary - UNSUPPORTED by SAP materialization
    # VARBINARY is blocked at Snowflake Iceberg level entirely
    if upper_type in ['BINARY', 'VARBINARY', 'BYTES']:
        raise ValueError(f"BINARY type not supported - VARBINARY blocked by Snowflake Iceberg, BINARY blocked by SAP materialization")
    
    # Default fallback
    return {"type": "cds.String", "length": 5000}
```

## Pre-Publish Checklist

Before calling `SYSTEM$SAP_PUBLISH_DATA_PRODUCT`:

- [ ] **Tables have ≥1 row of data** (parquet files must exist for materialization)
- [ ] CSN entity names match `DESCRIBE SHARE` table names exactly
- [ ] All INTEGER/BIGINT columns use `cds.Decimal(38,0)` (NOT cds.Integer)
- [ ] All TIME/TIMESTAMP columns use `cds.Timestamp` (NOT cds.Time/cds.DateTime)
- [ ] DECIMAL columns use exact precision from `INFORMATION_SCHEMA.COLUMNS`
- [ ] **No BINARY columns** (unsupported by SAP materialization)
- [ ] Context is SCHEMA name (e.g., `PUBLIC`), not database name
- [ ] All String columns have `length: 5000`

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
- INTEGER → `cds.Decimal(38,0)`
- BIGINT → `cds.Decimal(38,0)`
- TIME → `cds.Timestamp`
- TIMESTAMP_NTZ/LTZ → `cds.Timestamp`
- Reject BINARY columns with error

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
          "length": 5000,
          "key": true,
          "notNull": true,
          "@PersonalData.fieldSemantics": {"#": "DataSubjectIDType"}
        },
        "EMAIL": {
          "type": "cds.String",
          "length": 5000,
          "@PersonalData.isPotentiallyPersonal": true
        },
        "ORDER_COUNT": {
          "type": "cds.Decimal",
          "precision": 38,
          "scale": 0
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

### Error: "inputDataType=INTEGER, actualDataType=DECIMAL"

**Cause:** Using `cds.Integer` for INTEGER columns  
**Fix:** Use `cds.Decimal(38,0)` for all INTEGER/BIGINT columns

### Error: "inputDataType=TIME, actualDataType=TIMESTAMP"

**Cause:** Using `cds.Time` for TIME columns  
**Fix:** Use `cds.Timestamp` for TIME columns

### Error: "MISMATCH IN DATA TYPE PRECISION"

**Cause:** Using assumed precision (38) instead of actual  
**Fix:** Query `INFORMATION_SCHEMA.COLUMNS` for exact `NUMERIC_PRECISION`

### Error: Materialization fails for BINARY column

**Cause:** SAP cannot materialize Iceberg binary data to Delta format  
**Fix:** Exclude BINARY/VARBINARY columns from the data product

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
