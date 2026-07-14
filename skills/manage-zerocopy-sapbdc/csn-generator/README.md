# Minimal CSN Generator - SAP BDC Compatible

Generate minimal CSN (Core Schema Notation) Interop v1.0 files for maximum acceptance likelihood when publishing Snowflake data to SAP Datasphere/BDC.

## Why This Exists

Comprehensive CSN v1.2 files (with 68+ annotations, i18n translations, PII detection, etc.) may be rejected by SAP BDC due to:
- CSN version mismatch (1.2 vs 1.0)
- Over-annotation complexity
- Specific annotation format issues
- Type mapping differences

This skill generates minimal CSN Interop v1.0 with correct type mappings to maximize acceptance.

## What Gets Generated

### Sample Output

```json
{
  "csnInteropEffective": "1.0",
  "$schema": "2.0",
  "definitions": {
    "analytics": {
      "kind": "context"
    },
    "analytics.customers": {
      "kind": "entity",
      "elements": {
        "customer_id": {
          "type": "cds.String",
          "key": true,
          "notNull": true
        },
        "customer_name": {
          "type": "cds.String"
        },
        "email": {
          "type": "cds.String"
        },
        "created_date": {
          "type": "cds.Date"
        },
        "total_purchases": {
          "type": "cds.Decimal",
          "precision": 15,
          "scale": 2
        }
      }
    }
  },
  "i18n": {},
  "meta": {
    "creator": "Snowflake CSN Interop Generator - Minimal",
    "flavor": "inferred"
  }
}
```

### What's Included
- Core CSN structure (definitions, kind, elements)
- Primary key designation (`key: true`)
- Type mappings following the Snowflake → Iceberg → CSN chain
- Foreign key associations (if constraints available)
- One annotation: `@ObjectModel.foreignKey.association` (FK only)

### What's Omitted
- Display labels (`@EndUserText.label`)
- i18n translations
- Semantic annotations (`@Semantics.*`)
- PII detection (`@PersonalData.*`)
- Entity classification (`@ObjectModel.modelingPattern`)
- Analytical annotations (`@Aggregation.*`, `@AnalyticsDetails.*`)
- Text differentiation (`@ObjectModel.text.element`)

**Result:** ~300-500 bytes per entity (vs 3-5KB for full CSN)

## Structural Conventions

- CSN version `"1.0"` (not `"1.2"`)
- `$schema: "2.0"` (not full URL)
- Empty `i18n: {}` object
- `meta.flavor: "inferred"`
- Association names: lowercase without underscore prefix
- Cardinality: always `{"min": 0, "max": 1}`
- Strings: `cds.String` with **no length**
- Type mapping follows Snowflake → Iceberg → CSN (`INTEGER → cds.Integer`, `BIGINT → cds.Integer64`, `TIMESTAMP_*(6) → cds.Timestamp`)
- Float widening: `FLOAT`/`FLOAT4`/`FLOAT8` → `cds.Double`

## Quick Start

### 1. Invoke the Skill

```
Generate a minimal CSN from my Snowflake tables for SAP BDC publishing

Database: SALES_DB
Schema: ANALYTICS
Tables: customers, orders, products
Namespace: analytics
Output: ./outputs/analytics_minimal.csn.json
```

### 2. Publish to SAP BDC

**Snowflake:**
```sql
CALL SYSTEM$SAP_PUBLISH_DATA_PRODUCT(
    'analytics_product',
    '{
        "namespace": "analytics",
        "tables": ["customers", "orders", "products"],
        "csn_file": "s3://bucket/analytics_minimal.csn.json"
    }'
);
```

**Expected result:** Accepted

## Type Mapping Reference

| Snowflake Type | Iceberg Type | CDS Type | Notes |
|----------------|--------------|----------|-------|
| BOOLEAN | boolean | `cds.Boolean` | |
| INTEGER | int | `cds.Integer` | 32-bit |
| BIGINT | long | `cds.Integer64` | 64-bit |
| NUMBER(p,s) / DECIMAL(p,s) | decimal(p,s) | `cds.Decimal`, `precision: p`, `scale: s` | Exact precision/scale |
| FLOAT / FLOAT4 / FLOAT8 | float | `cds.Double` | MSA applies `floatToDoubleWidening` |
| REAL / DOUBLE PRECISION | double | `cds.Double` | |
| VARCHAR / STRING / TEXT | string | `cds.String` | **No length** |
| DATE | date | `cds.Date` | |
| TIMESTAMP_NTZ(6) / TIMESTAMP_LTZ(6) | timestamp / timestamptz | `cds.Timestamp` | Microsecond precision only |
| TIME | time | — | Not supported |
| TIMESTAMP_*(9) | timestamp_ns | — | Not supported |
| BINARY / BINARY(n) | binary / fixed(n) | — | Not supported |
| VARIANT / OBJECT / ARRAY / MAP | variant / struct / list / map | — | Not supported |
| GEOMETRY / GEOGRAPHY | geometry / geography | — | Not supported |

**Critical Differences:**
- INTEGER → `cds.Integer`; BIGINT → `cds.Integer64` (only `NUMBER`/`DECIMAL` → `cds.Decimal`)
- TIMESTAMP_*(6) → `cds.Timestamp` (no `cds.DateTime`/`cds.Time`)
- Strings → `cds.String` with **no length**
- `FLOAT`/`FLOAT4`/`FLOAT8` → `cds.Double`; `TIME`, nanosecond timestamps, `BINARY`, `VARIANT`, and complex types are rejected

## Association Example

**If foreign keys available:**
```json
{
  "elements": {
    "customer_id": {
      "type": "cds.String",
      "@ObjectModel.foreignKey.association": "customer"
    },
    "customer": {
      "type": "cds.Association",
      "target": "analytics.customers",
      "cardinality": {"min": 0, "max": 1},
      "on": [
        {"ref": ["customer", "customer_id"]},
        "=",
        {"ref": ["customer_id"]}
      ]
    }
  }
}
```

**Conventions:**
- Name: lowercase table name (no underscore)
- Cardinality: always optional (`min: 0`)
- ON clause: target → source direction
- If FK unavailable: NO associations

## Comparison Table

| Feature | Minimal CSN | Full CSN |
|---------|-------------|----------|
| **CSN Version** | 1.0 | 1.2 |
| **File size/entity** | ~500B | ~3-5KB |
| **Generation time** | <1s | 5-10s |
| **Annotations** | 0-1 | 68+ |
| **Display labels** | No | Yes |
| **i18n** | No | Yes |
| **PII detection** | No | Yes |
| **Entity classification** | No | Yes |
| **Associations** | Basic | Advanced |
| **SAP BDC acceptance** | High | Unknown |
| **SAP Datasphere value** | Low | High |

## Troubleshooting

### "INTEGER type not recognized"
**Solution:** Use `cds.Integer` for INTEGER and `cds.Integer64` for BIGINT; reserve `cds.Decimal(p,s)` for NUMBER/DECIMAL

### "TIMESTAMP type not recognized"
**Solution:** Use `cds.Timestamp` for microsecond timestamps; exclude TIME and nanosecond (`(9)`) timestamps

### "String type mismatch"
**Solution:** Emit `cds.String` with **no length** (never `cds.String(n)`)

### "Association target not found"
**Solution:** Use lowercase target name without underscore prefix

### "CSN version not supported"
**Solution:** Use `"1.0"` not `"1.2"`

## Known Limitations

These are **intentional** trade-offs for maximum SAP BDC acceptance:

1. No display labels → SAP UI shows raw column names
2. No semantic annotations → users enrich in SAP UI after import
3. No PII detection → no privacy annotations
4. No entity classification → SAP can't distinguish FACT/DIMENSION
5. Strings carry no length → SAP infers from the materialized column
6. Simple associations → always optional
7. CSN 1.0 limitations → missing features from 1.2
8. No heuristic inference → if FK unavailable, no associations
9. Unsupported types excluded → TIME, nanosecond timestamps, BINARY, VARIANT, and complex types

## Testing Recommendations

### Test 1: Minimal CSN (High Priority)
1. Generate minimal CSN from Snowflake tables
2. Publish to SAP BDC via `SYSTEM$SAP_PUBLISH_DATA_PRODUCT`
3. **Expected:** Accepted

### Test 2: Edge Cases
Test with:
- Tables without FK constraints (Iceberg tables)
- VARCHAR(16777216) columns (verify `cds.String` with no length)
- INTEGER columns (verify `cds.Integer`) and BIGINT columns (verify `cds.Integer64`)
- TIMESTAMP_NTZ(6) columns (verify `cds.Timestamp` acceptance)
- Unsupported columns (TIME, TIMESTAMP_*(9), BINARY, VARIANT) are excluded/rejected

## Next Steps

After generating minimal CSN:
1. Validate CSN structure (use validation checklist in SKILL.md)
2. Publish to SAP BDC
3. Verify data product appears in catalog
4. Test querying from SAP Datasphere
5. Document any acceptance issues

## References

- **CSN Interop Specification:** https://sap.github.io/csn-interop-specification/
- **Type Mapping:** See `references/type-mapping-sdk.md`

## Support

If SAP BDC rejects the minimal CSN:
1. Capture the **exact error message**
2. Document the CSN that was rejected
3. File issue with error details
4. Test incrementally (add annotations one by one)

---

**Goal:** Generate CSN that SAP BDC accepts on first try, every time.
