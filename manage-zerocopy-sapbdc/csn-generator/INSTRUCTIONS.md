---
name: sap-bdc-csn-generator
description: "Generate SAP CSN Interop v1.2 JSON from Snowflake metadata for SAP BDC integration. Triggers: SAP BDC, CSN interop, CSN json, generate CSN, Snowflake to SAP, data product, core schema notation."
parent_skill: manage-zerocopy-sapbdc
---

# SAP CSN Interop Generator

Generate SAP CSN Interop v1.2 JSON documents from Snowflake table metadata or Semantic Views, for publishing data products to SAP Business Data Cloud (BDC).

**When to use this skill:**
- Publishing Snowflake data as SAP BDC data products
- Creating CSN Interop documents for SAP integration scenarios
- Generating entity/element metadata with CSN Interop v1.2 annotation vocabularies (@ObjectModel, @Semantics, @PersonalData, @Aggregation, @EndUserText, @Consumption, @DataIntegration, @AnalyticsDetails, @ODM, @EntityRelationship)
- Converting Snowflake schemas to CDS-typed entity definitions with foreign key associations

**Quick example** — Input: Snowflake table at `ECOMMERCE.PRODUCT` with columns `PRODUCT (VARCHAR 40)`, `PRODUCT_NAME (VARCHAR 100)`, `PRODUCT_TYPE (VARCHAR 2)`, `CREATION_DATE (DATE)`. Output (abbreviated):
```json
{
  "csnInteropEffective": "1.2",
  "$version": "2.0",
  "definitions": {
    "ECOMMERCE": {
      "kind": "context"
    },
    "ECOMMERCE.PRODUCT": {
      "kind": "entity",
      "@EndUserText.label": "{i18n>ECOMMERCE.PRODUCT@ENDUSERTEXT.LABEL}",
      "elements": {
        "PRODUCT": {
          "key": true, "notNull": true, "type": "cds.String", "length": 40,
          "@EndUserText.label": "{i18n>ECOMMERCE.PRODUCT.PRODUCT@ENDUSERTEXT.LABEL}",
          "@ObjectModel.text.element": ["PRODUCT_NAME"]
        },
        "PRODUCT_NAME": {
          "type": "cds.String", "length": 100,
          "@EndUserText.label": "{i18n>ECOMMERCE.PRODUCT.PRODUCT_NAME@ENDUSERTEXT.LABEL}"
        },
        "PRODUCT_TYPE": {
          "type": "cds.String", "length": 2,
          "@EndUserText.label": "{i18n>ECOMMERCE.PRODUCT.PRODUCT_TYPE@ENDUSERTEXT.LABEL}"
        },
        "CREATION_DATE": {
          "type": "cds.Date",
          "@EndUserText.label": "{i18n>ECOMMERCE.PRODUCT.CREATION_DATE@ENDUSERTEXT.LABEL}"
        }
      }
    }
  },
  "i18n": { "en": { "ECOMMERCE.PRODUCT@ENDUSERTEXT.LABEL": "Product", "ECOMMERCE.PRODUCT.PRODUCT@ENDUSERTEXT.LABEL": "Product", "ECOMMERCE.PRODUCT.PRODUCT_NAME@ENDUSERTEXT.LABEL": "Product Name", "ECOMMERCE.PRODUCT.PRODUCT_TYPE@ENDUSERTEXT.LABEL": "Product Type", "ECOMMERCE.PRODUCT.CREATION_DATE@ENDUSERTEXT.LABEL": "Creation Date" } }
}
```

> 🔴 **Critical naming convention shown above**: namespace (`ECOMMERCE`) and all entity/element keys are UPPERCASE matching Snowflake's actual schema and column names. Only the i18n VALUES (the human-readable labels like "Product Name") are humanized. SAP Datasphere resolves remote tables by entity key — any case mismatch causes import to fail with "Remote object could not be found".

> **CRITICAL: SAP CDS CSN ≠ CSN Interop v1.2 — Annotation Vocabulary Boundary**
> 
> CSN Interop v1.2 uses **ONLY** these 10 extension vocabularies: `@Aggregation`, `@AnalyticsDetails`, `@Consumption`, `@DataIntegration`, `@EndUserText`, `@EntityRelationship`, `@ObjectModel`, `@ODM`, `@PersonalData`, `@Semantics`.
> 
> SAP S/4HANA CDS exports and SAP CAP `.cds` files use a **much larger** annotation vocabulary that is **NOT** compatible with CSN Interop. The `sample-csn-files/` directory contains SAP CDS CSN exports — do **not** copy annotation patterns from them.
> 
> **Non-spec annotations that must NEVER appear in generated CSN Interop output:**
> - `@Analytics.*` (entire vocabulary — not one of the 10 CSN Interop vocabularies)
> - `@ObjectModel.sapObjectNodeType.name` (SAP CDS only)
> - `@ObjectModel.usageType.dataClass` (SAP CDS only)
> - `@ObjectModel.usageType.serviceQuality` (SAP CDS only)
> - `@ObjectModel.dataCategory` (SAP CDS only — use `@ObjectModel.modelingPattern` instead)
> - `@Semantics.systemDate.*` / `@Semantics.systemDateTime.*` (SAP CDS only)
> - `@Semantics.user.*` (SAP CDS only)
> - `@Semantics.booleanIndicator` (SAP CDS only)
> Note: `@Semantics.time` IS in the CSN Interop spec (per `semantics.yaml`) and IS supported. Use it for TIMS-style 6-char time columns.

## Prerequisites

- Snowflake connection configured

## Workflow

### Step 1: Choose Generation Mode

**Ask** user which mode to use:

```
Select CSN generation mode:

1. Semantic View (recommended) - Generate CSN from an existing Snowflake Semantic View
2. Create Semantic View first - Create a Semantic View from tables, then generate CSN
3. Raw Tables - Generate CSN directly from INFORMATION_SCHEMA metadata
```

**If Option 1 (Semantic View):** Proceed to Step 2A
**If Option 2 (Create SV first):** Proceed to Step 2B
**If Option 3 (Raw Tables):** Proceed to Step 2C

### Step 2A: Generate from Existing Semantic View

**Ask** user for:
- Database name
- Schema name
- Semantic View name
- Namespace name (default: **UPPERCASE Snowflake schema name** as it appears in `INFORMATION_SCHEMA`, e.g., `ECOMMERCE`, `SALES` — must match the actual Snowflake schema for SAP Datasphere to resolve remote tables)
- Output file path (default: `./outputs/<schema>.csn.json`)

**Execute these SQL queries** using `snowflake_sql_execute`:

```sql
DESCRIBE SEMANTIC VIEW "<DATABASE>"."<SCHEMA>"."<SEMANTIC_VIEW_NAME>";
```

Optionally get DDL for context:
```sql
SELECT GET_DDL('SEMANTIC VIEW', '"<DATABASE>"."<SCHEMA>"."<SEMANTIC_VIEW_NAME>"');
```

**Then**: Construct the CSN JSON following [references/csn-construction-rules.md](references/csn-construction-rules.md).

**Proceed to** Step 3.

### Step 2B: Create Semantic View, Then Generate CSN

1. **Ask** user for database, schema, and which tables to include.
2. **Invoke** the `semantic-view` skill to create a Semantic View from the specified tables.
3. Once the Semantic View is created, proceed with Step 2A using the newly created view.

### Step 2C: Generate from Raw Table Metadata

**Ask** user for:
- Database name
- Schema name (if left blank, run `SHOW SCHEMAS IN DATABASE` and present options)
- Table names (comma-separated, or leave blank for all tables in schema)
- Namespace name (default: **UPPERCASE Snowflake schema name** as it appears in `INFORMATION_SCHEMA`, e.g., `ECOMMERCE`, `SALES` — must match the actual Snowflake schema for SAP Datasphere to resolve remote tables)
- Output file path (default: `./outputs/<schema>.csn.json`)

**Execute these SQL queries** using `snowflake_sql_execute`:

#### Query 0 (if schema is blank): Discover schemas

```sql
SHOW SCHEMAS IN DATABASE "<DATABASE>";
```

Present non-system schemas (exclude `INFORMATION_SCHEMA`) and ask user to pick one.

**IMPORTANT: Schema names in Snowflake are case-sensitive.** Use the exact schema name returned by `SHOW SCHEMAS` in all subsequent queries.

#### Query 1: Get table list

```sql
SELECT t.TABLE_NAME, t.COMMENT
FROM "<DATABASE>".INFORMATION_SCHEMA.TABLES t
WHERE t.TABLE_SCHEMA = '<SCHEMA>'
  AND t.TABLE_TYPE IN ('BASE TABLE', 'VIEW')
ORDER BY t.TABLE_NAME;
```

#### Query 2: Get column metadata

```sql
SELECT c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, c.CHARACTER_MAXIMUM_LENGTH,
       c.NUMERIC_PRECISION, c.NUMERIC_SCALE, c.IS_NULLABLE, c.COLUMN_DEFAULT,
       c.COMMENT, c.ORDINAL_POSITION
FROM "<DATABASE>".INFORMATION_SCHEMA.COLUMNS c
WHERE c.TABLE_SCHEMA = '<SCHEMA>'
ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION;
```

#### Query 3: Get primary keys

```sql
SELECT tc.TABLE_NAME, kcu.COLUMN_NAME
FROM "<DATABASE>".INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
JOIN "<DATABASE>".INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
  ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
  AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
WHERE tc.TABLE_SCHEMA = '<SCHEMA>'
  AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
ORDER BY tc.TABLE_NAME, kcu.ORDINAL_POSITION;
```

#### Query 4: Get foreign keys

```sql
SELECT rc.TABLE_NAME AS source_table,
       kcu.COLUMN_NAME AS source_column,
       fk_kcu.TABLE_NAME AS target_table,
       fk_kcu.COLUMN_NAME AS target_column
FROM "<DATABASE>".INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
JOIN "<DATABASE>".INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
  ON rc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
  AND rc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA
JOIN "<DATABASE>".INFORMATION_SCHEMA.KEY_COLUMN_USAGE fk_kcu
  ON rc.UNIQUE_CONSTRAINT_NAME = fk_kcu.CONSTRAINT_NAME
  AND rc.UNIQUE_CONSTRAINT_SCHEMA = fk_kcu.CONSTRAINT_SCHEMA
WHERE rc.CONSTRAINT_SCHEMA = '<SCHEMA>'
ORDER BY rc.TABLE_NAME, kcu.ORDINAL_POSITION;
```

Note: Query 3 and Query 4 may fail if the user lacks access to constraint views. If they fail, proceed without PK/FK information and note this in the output summary.

#### Query 5: Discover masking policies (for @PersonalData annotations)

```sql
SELECT * FROM TABLE(
  "<DATABASE>".INFORMATION_SCHEMA.POLICY_REFERENCES(
    REF_ENTITY_NAME => '<DATABASE>.<SCHEMA>.<TABLE_NAME>',
    REF_ENTITY_DOMAIN => 'TABLE'
  )
);
```

Record which columns have active masking policies — these are confirmed PII fields. Optionally check for classification tags via `TAG_REFERENCES`.

#### Query 6: Get row counts (for @ObjectModel.usageType.sizeCategory)

```sql
SELECT TABLE_NAME, ROW_COUNT
FROM "<DATABASE>".INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = '<SCHEMA>'
  AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
ORDER BY TABLE_NAME;
```

#### Post-Query: Filter CDC/Pipeline Columns

After retrieving column metadata, **exclude** columns matching these patterns:

| Pattern | Source |
|---|---|
| `__OPERATION_TYPE`, `__TIMESTAMP` | Openflow CDC |
| `LOAD_TYPE_*`, `RUN_ID_*` | Openflow pipeline |
| `_FIVETRAN_SYNCED`, `_FIVETRAN_DELETED` | Fivetran CDC |
| `_SDC_*` | Stitch/Singer CDC |
| `_AIRBYTE_AB_ID`, `_AIRBYTE_EMITTED_AT` | Airbyte CDC |

Count and report filtered columns in the Step 3 summary.

**Then**: Construct the CSN JSON following [references/csn-construction-rules.md](references/csn-construction-rules.md).

**Proceed to** Step 3.

---

## CSN Construction Rules

**IMPORTANT**: Read [references/csn-construction-rules.md](references/csn-construction-rules.md) for the complete, detailed construction rules including:
- Root structure and `meta.creator` field
- Namespace and entity definitions
- Mandatory entity-level annotations
- Display-label convention (Original or PascalCase, label-only — entity/element keys always UPPERCASE)
- Element definitions with i18n placeholder format and text differentiation rules
- String length handling
- Foreign key associations and cardinality rules
- Auto-inferred semantic annotations (@Semantics, @ObjectModel, @PersonalData, etc.)
- Auto-inferred analytical annotations (FACT/DIMENSION/TEXT classification)
- Auto-inferred associations (heuristic patterns when PK/FK queries fail)
- Snowflake to CDS type mapping

Also see:
- [references/annotation-patterns.md](references/annotation-patterns.md) — Column-name-to-annotation pattern tables
- [references/snowflake-type-mapping.md](references/snowflake-type-mapping.md) — Complete type mapping
- [references/csn-spec-reference.md](references/csn-spec-reference.md) — CSN Interop v1.2 quick reference
- [references/pascal-case-dictionary.md](references/pascal-case-dictionary.md) — SAP word dictionary for PascalCase conversion
- [references/supported-features.md](references/supported-features.md) — Feature support matrix

---

### Step 3: Review and Validate Output

After constructing the CSN JSON:

1. **Validate** the structure:
   - `csnInteropEffective` = `"1.2"`, `$version` = `"2.0"`, `meta.creator` exists
   - Every definition has `"kind"`, first definition uses `kind: context`
   - Every entity has `"elements"`, every element has `"type"`
   - Every key element has both `"key": true` and `"notNull": true`
   - All enum annotations use `{"#": "VALUE"}` notation
   - `@PersonalData` uses camelCase keys
   - `@ObjectModel.foreignKey.association` uses **Element Reference format** `{ "=": "_TargetEntity" }` (NOT direct string)
   - `@ObjectModel.text.association` uses **Element Reference format** `{ "=": "_Composition" }`
   - `@ObjectModel.semanticKey` uses **Element Reference array** `[ { "=": "KEY_COLUMN" } ]`
   - 🔴 **`@ObjectModel.text.element` uses PLAIN STRING ARRAY** `[ "COLUMN_NAME" ]` — NOT `[ { "=": "COLUMN_NAME" } ]`. Wrong format crashes SAP Datasphere parser at import time with `"Cannot read properties of undefined (reading 'name')"`. This is the OPPOSITE format of the three annotations above — verify carefully.
   - Association names use `_` prefix; `on` clauses follow `[association_target_field, =, source_field]` direction (target navigation first, source second) per CSN Interop spec
   - `@EndUserText` label/heading/quickInfo are NOT all identical on any element
   - Every entity has all mandatory entity-level annotations
   - No non-spec annotations present (see CRITICAL note above)
   - No text tables have `@ObjectModel.compositionRoot: true`
   - Root-level `"i18n"` object exists with `"en"` translations for all placeholders
   - No CDC/pipeline columns leaked into the output

2. **Present** summary to user:
   - Entity and element counts
   - CDC columns filtered (count)
   - String length handling and naming convention used
   - Type mapping warnings, PK/FK status
   - Associations detected/inferred, annotations applied
   - Bridge/link tables detected, @PersonalData annotations applied

3. **Write** the JSON to the specified output file path.

**Stop**: Get user approval before proceeding to mandatory reviews.

### Step 4: Mandatory Reviews and Optional Enhancements

#### 4A: Review Associations (MANDATORY)

Present inferred associations for user review:

```
| Source Entity | Association | Target Entity | Cardinality | Method |
|---|---|---|---|---|
| ProductPlant | _Product | Product | max: 1 | Heuristic (shared Product) |
```

**Ask** user: Are all correct? Any to remove/add? Any cardinality changes?

#### 4B: Review @PersonalData Annotations (MANDATORY)

Present PII summary for user confirmation:

```
| Entity | Element | Detection Source | entitySemantics | fieldSemantics |
|---|---|---|---|---|
| DocsPerms | UserEmails | Masking policy | DATA_SUBJECT_DETAILS | DATA_SUBJECT_ID_TYPE |
```

**Ask** user: All correct? Any false positives? Any missed PII?

#### 4C: Optional Enhancements

After mandatory reviews, **ask** user if they want any of:

1. **Refine analytical annotations** — Adjust FACT/DIMENSION classifications and aggregation defaults
2. **Refine currency/unit semantics** — Correct amount-to-currency and quantity-to-unit linkages
3. **Add i18n languages** — Add translations beyond `"en"`
4. **Customize namespace** — Rename entities, change namespace, add doc strings
5. **Add @ODM annotations** — Map entities to SAP One Domain Model
6. **Add @EntityRelationship annotations** — Cross-boundary entity references

Read the CSN JSON file, apply changes, and write it back.

### Step 5: Deliver Output

1. **Read** the final CSN JSON file.
2. **Validate** the structure (same checks as Step 3).
3. **Present** final summary: file path, size, entity/element counts, annotations summary, warnings.

## Known Limitations

1. VARIANT/OBJECT/ARRAY/GEOGRAPHY/GEOMETRY/VECTOR → `cds.LargeString` (no CDS equivalent)
2. Semantic View associations not exposed via DESCRIBE — use raw tables mode or add manually
3. Heuristic associations may produce false positives — always reviewed in Step 4A
4. PascalCase splitting unreliable for concatenated names — build explicit lookup dictionaries
5. Iceberg/CLD/Openflow tables often report VARCHAR max (16777216) — skill detects and offers alternatives
6. Custom types (`kind: type`) not auto-generated
7. @ODM / @EntityRelationship require SAP-specific knowledge — manual in Step 4
8. @PersonalData uses CSN Interop convention (camelCase + enum), not CAP CDS convention

## Stopping Points

- After Step 1: mode selection confirmed
- After Element Naming Convention question: naming choice confirmed
- After Step 3: review generated CSN before mandatory reviews
- After Step 4A/4B: association and PII reviews approved before optional enhancements

## Output

A valid SAP CSN Interop v1.2 JSON file in the `./outputs/` directory, ready for import into SAP BDC or other SAP ecosystem tools.
