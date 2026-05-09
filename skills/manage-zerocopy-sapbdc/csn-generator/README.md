# Snowflake -> SAP BDC CSN Interop Generator

**Authors:** CoCo + Snowflake | 6-May-2026


A Cortex Code skill that generates [SAP CSN Interop v1.2](https://sap.github.io/csn-interop-specification/) JSON files from Snowflake table metadata or Snowflake Semantic Views, that can be used when publishing data products from Snowflake to SAP Business Data Cloud (BDC).

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Step-by-Step Walkthrough](#step-by-step-walkthrough)
  - [Step 1: Choose Generation Mode](#step-1-choose-generation-mode)
  - [Step 2: Metadata Collection](#step-2-metadata-collection)
  - [Step 3: Review and Validate](#step-3-review-and-validate)
  - [Step 4: Mandatory Reviews and Optional Enhancements](#step-4-mandatory-reviews-and-optional-enhancements)
  - [Step 5: Deliver Output](#step-5-deliver-output)
- [What Gets Generated](#what-gets-generated)
- [How the CSN Gets Generated](#how-the-csn-gets-generated)
  - [Element Naming](#element-naming)
  - [Entity Classification](#entity-classification)
  - [Association Inference](#association-inference)
  - [PII Detection](#pii-detection-two-tier)
  - [String Length Handling](#string-length-handling)
  - [CDC/Pipeline Column Filtering](#cdcpipeline-column-filtering)
- [Annotation Vocabularies](#annotation-vocabularies)
- [Snowflake to CDS Type Mapping](#snowflake-to-cds-type-mapping)
- [Common Pitfalls](#common-pitfalls)
- [Troubleshooting](#troubleshooting)
- [Known Limitations](#known-limitations)
- [FAQ](#faq)
- [File Structure](#file-structure)
- [References](#references)

## Overview

This Cortex Code skill reads Snowflake table metadata (columns, types, keys, masking policies, tags, row counts) and produces a fully-annotated CSN Interop Effective v1.2 JSON file. The output is ready for import into SAP BDC, Datasphere, or any SAP tool that consumes CSN.

**Key capabilities:**
- Three generation modes: from a Semantic View, by creating a Semantic View first, or directly from raw INFORMATION_SCHEMA metadata
- Auto-infers 40+ annotation types across all 10 CSN Interop extension vocabularies
- Detects PII columns from Snowflake masking policies and column name heuristics
- Classifies entities as FACT, DIMENSION, TEXT, bridge/link, or general data structures
- Infers foreign key associations heuristically when constraint metadata is unavailable
- Filters CDC/pipeline metadata columns (Openflow, Fivetran, Stitch, Airbyte)
- Display-label convention choice: Original (preserve source, default) or PascalCase (cosmetic SAP-style labels for `@EndUserText.label` only) with 200+ word SAP-aware dictionary. Entity and element keys are always UPPERCASE matching Snowflake — required for SAP Datasphere remote-table resolution.
- Handles Iceberg, catalog-linked, and external table sources with smart string length detection
- Uses `kind: context` namespacing (SAP BDC standard)
- Maps UUID/GUID columns to `cds.UUID`
- i18n placeholders by default with `{i18n>Entity.Element@ENDUSERTEXT.LABEL}` format and English translations
- Interactive refinement workflow with post-generation enhancement options

No Python or external dependencies required — the skill runs entirely through Cortex Code using SQL queries and inline JSON construction.

## Prerequisites

1. **Snowflake connection** configured in your Cortex Code environment
2. **Read access** to `INFORMATION_SCHEMA` views in the target database (TABLES, COLUMNS, TABLE_CONSTRAINTS, KEY_COLUMN_USAGE, REFERENTIAL_CONSTRAINTS)
3. **Optional**: Access to `POLICY_REFERENCES` and `TAG_REFERENCES` table functions for PII detection

> **Note**: The skill degrades gracefully. If constraint views are unavailable (common with Iceberg/catalog-linked tables), it falls back to heuristic association inference. If policy/tag functions are unavailable, PII detection relies solely on column name patterns.

## Getting Started

Simply ask Cortex Code:

```
Generate a CSN Interop file from my Snowflake tables
```

Or be more specific:

```
Generate a CSN file for database SALES_DB, schema ANALYTICS
```

```
Create a CSN Interop document from my Semantic View SALES_DB.ANALYTICS.SALES_MODEL
```

The skill will guide you interactively through mode selection, configuration options, and output generation.

## Step-by-Step Walkthrough

### Step 1: Choose Generation Mode

The skill presents three modes:

| Mode | When to Use | Input Required |
|---|---|---|
| **1. Semantic View** (recommended) | You already have a Snowflake Semantic View | Database, schema, view name |
| **2. Create Semantic View first** | You want the richest output from raw tables | Database, schema, table names |
| **3. Raw Tables** | Quick generation without a Semantic View | Database, schema, optional table filter |

**Common configuration options** (asked after mode selection):
- **Namespace name**: UPPERCASE Snowflake schema name as it appears in `INFORMATION_SCHEMA` (e.g., `LEDGER`, `PRODUCT`). MUST match the actual Snowflake schema path so SAP Datasphere can resolve remote tables. Always uses `kind: context` (SAP BDC standard)
- **Naming convention**: Original (preserve source, default) or PascalCase (SAP standard) — the skill asks you to choose
- **String length handling**: Omit, SAP defaults, or keep Snowflake defaults (asked when all TEXT columns share a uniform length)
- **Output file path**: Default `./outputs/<schema>.csn.json`

### Step 2: Metadata Collection

Depending on the mode, the skill executes SQL queries to gather metadata.

**Mode 1 (Semantic View):**
1. `DESCRIBE SEMANTIC VIEW` — entity and column metadata
2. `GET_DDL('SEMANTIC VIEW', ...)` — full DDL context (optional)

**Mode 3 (Raw Tables)** runs up to 7 queries:

| Query | Purpose | Fallback |
|---|---|---|
| Query 0 | `SHOW SCHEMAS IN DATABASE` — discover schemas | Only if schema is blank or returns 0 rows |
| Query 1 | `INFORMATION_SCHEMA.TABLES` — table list + comments | Required |
| Query 2 | `INFORMATION_SCHEMA.COLUMNS` — column metadata (types, lengths, nullability) | Required |
| Query 3 | `TABLE_CONSTRAINTS` + `KEY_COLUMN_USAGE` — primary keys | Falls back to heuristic key inference |
| Query 4 | `REFERENTIAL_CONSTRAINTS` + `KEY_COLUMN_USAGE` — foreign keys | Falls back to heuristic association inference |
| Query 5 | `POLICY_REFERENCES` + `TAG_REFERENCES` — masking policies and classification tags | Falls back to column name heuristics for PII |
| Query 6 | `INFORMATION_SCHEMA.TABLES.ROW_COUNT` — row counts for size category | Omits `@ObjectModel.usageType.sizeCategory` if NULL |

After queries, CDC/pipeline columns are automatically filtered out before CSN construction.

### Step 3: Review and Validate

The skill presents a generation summary including:
- Entity and element counts
- CDC columns filtered
- Naming convention and string length decisions
- Auto-inferred annotations applied (by category)
- Association detection method (constraints vs heuristic)
- Bridge/link tables detected
- PII annotations applied (masking policy vs heuristic)
- Any warnings (failed queries, type mapping issues)

The CSN JSON is written to disk. **The skill stops here for your approval before proceeding.**

### Step 4: Mandatory Reviews and Optional Enhancements

**Mandatory reviews** (always presented):

- **4A: Review Associations** — The skill presents a table of all inferred associations (source → target, cardinality, detection method). You confirm correctness, remove false positives, add missing relationships, or adjust cardinality. This is critical because heuristic associations are the highest-risk auto-inference.
- **4B: Review @PersonalData** — The skill presents a PII summary table (entity, element, detection source, annotations). You confirm PII classifications, correct false positives/negatives, and optionally add `dataSubjectRole`. This is critical because PII misclassification has compliance consequences.

**Optional enhancements** (offered after mandatory reviews):

1. **Refine analytical annotations** — adjust FACT/DIMENSION classifications, aggregation defaults
2. **Refine currency/unit semantics** — correct auto-inferred amount/quantity links
3. **Add i18n languages** — add translations beyond English to the root `i18n` block
4. **Customize namespace** — rename entities, change namespace name, add descriptions
5. **Add @ODM annotations** — map to SAP One Domain Model (requires SAP ODM knowledge)
6. **Add @EntityRelationship annotations** — cross-boundary entity references (requires SAP VDM URIs)

### Step 5: Deliver Output

Final validation and delivery with file path, size, entity/element counts, and annotation summary.

## What Gets Generated

A single JSON file following [CSN Interop Effective v1.2](https://sap.github.io/csn-interop-specification/spec-v1/csn-interop-effective):

```
./outputs/<schema>.csn.json
```

### Document Structure

```json
{
  "$schema": "https://sap.github.io/csn-interop-specification/spec-v1/csn-interop-effective.schema.json",
  "csnInteropEffective": "1.2",
  "$version": "2.0",
  "meta": {
    "document": {
      "version": "1.0.0",
      "title": "<DATABASE>.<SCHEMA> Data Model",
      "doc": "CSN Interop document generated from Snowflake ..."
    },
    "creator": "Snowflake CSN Interop Generator (Cortex Code Skill) v10.0.0",
    "features": { "complete": true }
  },
  "definitions": {
    "<namespace>": { "kind": "context" },
    "<namespace>.<Entity1>": { "kind": "entity", "elements": { ... } },
    "<namespace>.<Entity2>": { "kind": "entity", "elements": { ... } }
  }
}
```

### Entity Definition Example

Based on Snowflake table `SANAGAMA_SAP_PRODUCT.PRODUCT.PRODUCT` (database `SANAGAMA_SAP_PRODUCT`, schema `PRODUCT`, table `PRODUCT`):

```json
"PRODUCT.PRODUCT": {
  "kind": "entity",
  "@EndUserText.label": "{i18n>PRODUCT.PRODUCT@ENDUSERTEXT.LABEL}",
  "@ObjectModel.modelingPattern": { "#": "ANALYTICAL_DIMENSION" },
  "@ObjectModel.supportedCapabilities": [
    { "#": "ANALYTICAL_DIMENSION" }, { "#": "SQL_DATA_SOURCE" },
    { "#": "CDS_MODELING_DATA_SOURCE" }, { "#": "CDS_MODELING_ASSOCIATION_TARGET" }
  ],
  "@ObjectModel.tenantWideUniqueName": "SANAGAMA_SAP_PRODUCT.PRODUCT.PRODUCT",
  "@ObjectModel.semanticKey": [{ "=": "PRODUCT" }],
  "@ObjectModel.usageType.sizeCategory": { "#": "M" },
  "elements": {
    "PRODUCT": {
      "type": "cds.String", "length": 40,
      "key": true, "notNull": true,
      "@EndUserText.label": "{i18n>PRODUCT.PRODUCT.PRODUCT@ENDUSERTEXT.LABEL}",
      "@EndUserText.heading": "{i18n>PRODUCT.PRODUCT.PRODUCT@ENDUSERTEXT.HEADING}",
      "@EndUserText.quickInfo": "{i18n>PRODUCT.PRODUCT.PRODUCT@ENDUSERTEXT.QUICKINFO}",
      "@ObjectModel.text.element": ["PRODUCT_NAME"]
    },
    "PRODUCT_NAME": {
      "type": "cds.String", "length": 40,
      "@EndUserText.label": "{i18n>PRODUCT.PRODUCT.PRODUCT_NAME@ENDUSERTEXT.LABEL}",
      "@EndUserText.heading": "{i18n>PRODUCT.PRODUCT.PRODUCT_NAME@ENDUSERTEXT.HEADING}",
      "@EndUserText.quickInfo": "{i18n>PRODUCT.PRODUCT.PRODUCT_NAME@ENDUSERTEXT.QUICKINFO}",
      "@Semantics.text": true
    },
    "PRODUCT_UUID": {
      "type": "cds.UUID",
      "@EndUserText.label": "{i18n>PRODUCT.PRODUCT.PRODUCT_UUID@ENDUSERTEXT.LABEL}",
      "@EndUserText.heading": "{i18n>PRODUCT.PRODUCT.PRODUCT_UUID@ENDUSERTEXT.HEADING}",
      "@EndUserText.quickInfo": "{i18n>PRODUCT.PRODUCT.PRODUCT_UUID@ENDUSERTEXT.QUICKINFO}"
    },
    "IS_MARKED_FOR_DELETION": {
      "type": "cds.Boolean",
      "@EndUserText.label": "{i18n>PRODUCT.PRODUCT.IS_MARKED_FOR_DELETION@ENDUSERTEXT.LABEL}",
      "@EndUserText.heading": "{i18n>PRODUCT.PRODUCT.IS_MARKED_FOR_DELETION@ENDUSERTEXT.HEADING}",
      "@EndUserText.quickInfo": "{i18n>PRODUCT.PRODUCT.IS_MARKED_FOR_DELETION@ENDUSERTEXT.QUICKINFO}"
    },
    "CREATION_DATE": {
      "type": "cds.Date",
      "@EndUserText.label": "{i18n>PRODUCT.PRODUCT.CREATION_DATE@ENDUSERTEXT.LABEL}",
      "@EndUserText.heading": "{i18n>PRODUCT.PRODUCT.CREATION_DATE@ENDUSERTEXT.HEADING}",
      "@EndUserText.quickInfo": "{i18n>PRODUCT.PRODUCT.CREATION_DATE@ENDUSERTEXT.QUICKINFO}"
    },
    "GROSS_WEIGHT": {
      "type": "cds.Decimal", "precision": 13, "scale": 3,
      "@EndUserText.label": "{i18n>PRODUCT.PRODUCT.GROSS_WEIGHT@ENDUSERTEXT.LABEL}",
      "@EndUserText.heading": "{i18n>PRODUCT.PRODUCT.GROSS_WEIGHT@ENDUSERTEXT.HEADING}",
      "@EndUserText.quickInfo": "{i18n>PRODUCT.PRODUCT.GROSS_WEIGHT@ENDUSERTEXT.QUICKINFO}",
      "@Semantics.quantity.unitOfMeasure": "WEIGHT_UNIT"
    },
    "WEIGHT_UNIT": {
      "type": "cds.String", "length": 3,
      "@EndUserText.label": "{i18n>PRODUCT.PRODUCT.WEIGHT_UNIT@ENDUSERTEXT.LABEL}",
      "@EndUserText.heading": "{i18n>PRODUCT.PRODUCT.WEIGHT_UNIT@ENDUSERTEXT.HEADING}",
      "@EndUserText.quickInfo": "{i18n>PRODUCT.PRODUCT.WEIGHT_UNIT@ENDUSERTEXT.QUICKINFO}",
      "@Semantics.unitOfMeasure": true
    },
    "PRODUCT_GROUP": {
      "type": "cds.String", "length": 9,
      "@EndUserText.label": "{i18n>PRODUCT.PRODUCT.PRODUCT_GROUP@ENDUSERTEXT.LABEL}",
      "@ObjectModel.foreignKey.association": { "=": "_PRODUCT_GROUP" }
    },
    "_PRODUCT_GROUP": {
      "type": "cds.Association",
      "target": "PRODUCT.PRODUCT_GROUP",
      "cardinality": { "min": 1, "max": 1 },
      "on": [
        { "ref": ["_PRODUCT_GROUP", "PRODUCT_GROUP"] }, "=",
        { "ref": ["PRODUCT_GROUP"] }
      ]
    }
  }
}
```

> **Note**: All entity and element keys are UPPERCASE matching Snowflake's actual naming. The `_PRODUCT_GROUP` association uses `[target, =, source]` ON clause direction per CSN Interop spec, and `@ObjectModel.foreignKey.association` uses Element Reference format `{ "=": "..." }`. Only the i18n VALUES (the `"en": { ... }` block, not shown above) contain humanized display labels.

## How the CSN Gets Generated

### Display-Label Convention

> ⚠️ **Entity and element KEYS are always UPPERCASE matching Snowflake.** The convention below only affects display LABELS (`@EndUserText.label` values) — never the keys. Using PascalCase keys breaks SAP Datasphere remote-table resolution.

| Option | Snowflake Source | Entity Key | Element Key | `@EndUserText.label` (display) |
|---|---|---|---|---|
| **Original** (preserve, default) | table `ORDERS`, column `ORDER_ID` | `ORDERS` | `ORDER_ID` | `ORDER_ID` |
| **PascalCase** (label-only) | table `ORDERS`, column `ORDER_ID` | `ORDERS` | `ORDER_ID` | `OrderId` |
| **PascalCase** (label-only) | table `PRODUCT`, column `PRODUCTEXTERNALID` | `PRODUCT` | `PRODUCTEXTERNALID` | `ProductExternalId` |

PascalCase label generation builds an explicit `KNOWN_COLUMNS` dictionary mapping each ALL_UPPERCASE column name to its correct PascalCase form. This is necessary because greedy dictionary splitting is unreliable for SAP concatenated names containing short words (AT, IN, OF, IS, TO, BY). A 200+ word SAP-aware dictionary in [references/pascal-case-dictionary.md](references/pascal-case-dictionary.md) is provided as a reference for building the lookup.

### Entity Classification

| Classification | Trigger | modelingPattern | Example |
|---|---|---|---|
| **FACT** | 10+ measure columns, or name contains `transaction`/`journal`/`ledger` | `ANALYTICAL_FACT` | `JournalEntry` |
| **DIMENSION** | Master data name patterns, <30% numeric columns, descriptive strings | `ANALYTICAL_DIMENSION` | `Product`, `Customer` |
| **TEXT** | Has `LANGUAGE` column + parent entity key | `LANGUAGE_DEPENDENT_TEXT` | `ProductDescription` |
| **Bridge/Link** | Only FK columns, no descriptive attributes, name joins two entities | `DATA_STRUCTURE` | `ProdIntlTradeClassification` |
| **General** | None of the above | `DATA_STRUCTURE` | Fallback |

### Association Inference

When formal PK/FK constraints are available, associations are created directly. When constraints are unavailable (Iceberg, external tables), the skill infers them via:

1. **Shared key column** — if column `Product` appears in multiple entities and entity `Product` exists, create `_Product` associations
2. **Text table pattern** — entities ending with `_DESCRIPTION` / `_TEXT` (or `description` / `text`) with a `LANGUAGE` column. Creates a `cds.Composition` on the parent entity. **Important**: compositions MUST include an `on` clause linking the parent key to the child key, with target navigation FIRST and source SECOND per CSN Interop spec (e.g., `"on": [{"ref": ["_PRODUCT_DESCRIPTION", "PRODUCT"]}, "=", {"ref": ["PRODUCT"]}]`). Omitting the `on` clause causes validation errors. Reversing the direction may cause association resolution failures.
3. **Group/classification pattern** — entities like `ProductGroup` with distinct key columns

**Cardinality rules:**
- `{ "min": 1, "max": 1 }` (mandatory to-one): when the FK column is NOT NULL
- `{ "max": 1 }` (optional to-one): when the FK column is nullable
- `{ "min": 1, "max": "*" }` (mandatory to-many): for reverse/navigational on NOT NULL columns
- `{ "max": "*" }` (optional to-many): for nullable reverse associations

### PII Detection (Two-Tier)

| Source | Method | Confidence |
|---|---|---|
| **Masking policies** | `POLICY_REFERENCES` table function per table | High — confirmed PII |
| **Classification tags** | `TAG_REFERENCES` with SEMANTIC_CATEGORY/PRIVACY_CATEGORY | High |
| **Column name heuristics** | Pattern matching on `*EMAIL*`, `*SSN*`, `*PHONE*`, `*HASH*`, etc. | Medium — inferred PII |

### String Length Handling

The skill detects when all TEXT columns share a uniform length (e.g., 16,777,216 for Iceberg sources) and offers two options:
- **Use `cds.LargeString`** — `"type": "cds.LargeString"` with no `length` property (recommended for Iceberg/external sources). The CSN Interop schema caps `cds.String.length` at 5000, so values above that must use `cds.LargeString`.
- **SAP defaults** — apply SAP-typical lengths (40 for IDs, 256 for descriptions) based on column name heuristics. Keeps `cds.String` with a spec-valid length ≤ 5000.

### CDC/Pipeline Column Filtering

Internal metadata columns are automatically excluded:

| Source | Filtered Columns |
|---|---|
| Openflow | `__OPERATION_TYPE`, `__TIMESTAMP`, `LOAD_TYPE_*`, `RUN_ID_*` |
| Fivetran | `_FIVETRAN_SYNCED`, `_FIVETRAN_DELETED` |
| Stitch/Singer | `_SDC_*` |
| Airbyte | `_AIRBYTE_AB_ID`, `_AIRBYTE_EMITTED_AT` |

## Annotation Vocabularies

> **CRITICAL: Only CSN Interop v1.2 spec annotations are generated.** SAP CDS CSN uses a much larger annotation vocabulary. Annotations from SAP S/4HANA CDS exports — including `@Analytics.*`, `@ObjectModel.sapObjectNodeType.name`, `@ObjectModel.usageType.dataClass/serviceQuality`, `@ObjectModel.dataCategory`, `@Semantics.systemDate.*`, `@Semantics.systemDateTime.*`, `@Semantics.user.*`, `@Semantics.booleanIndicator` — are **NOT** in the 10 CSN Interop vocabularies and are never generated. (`@Semantics.time` IS in the CSN Interop spec and IS supported.)

The skill supports all 10 [CSN Interop extension vocabularies](https://sap.github.io/csn-interop-specification/spec-v1/extensions). All enum annotations use `{"#": "VALUE"}` notation per the spec.

### 1. @EndUserText

Applied to every entity and element. Provides human-readable labels.

| Annotation | Applied To | Source |
|---|---|---|
| `@EndUserText.label` | Entities + Elements | `{i18n>Entity.Element@ENDUSERTEXT.LABEL}` placeholder; English value humanized from name |
| `@EndUserText.heading` | Elements | `{i18n>...HEADING}` placeholder; abbreviated column heading (shorter than label) |
| `@EndUserText.quickInfo` | Elements | `{i18n>...QUICKINFO}` placeholder; column comment or longer description (always more descriptive than label) |

### 2. @Semantics

Auto-detected from column name patterns:

| Category | Annotations | Column Patterns |
|---|---|---|
| Business dates | `.businessDate.from`, `.to` | `VALID_FROM`, `VALID_TO` |
| Currency | `.currencyCode`, `.amount.currencyCode` | `CURRENCY`, `*PRICE*`, `*AMOUNT*` |
| Units | `.unitOfMeasure`, `.quantity.unitOfMeasure` | `*UNIT`, `*QUANTITY*`, `*WEIGHT*` |
| Language | `.language` | `LANGUAGE` |
| Text | `.text` | Columns ending with `NAME`, `DESCRIPTION`, `TEXT`, `TITLE` |
| Calendar | `.calendar.year`, `.quarter`, `.month`, `.week`, `.dayOfMonth` | `*CALENDAR_YEAR*`, `*CALMONTH*`, etc. |
| Fiscal | `.fiscal.year`, `.period`, `.quarter`, `.yearPeriod` | `*FISCAL_YEAR*`, `*FISCAL_PERIOD*`, etc. |
| Contact | `.eMail.address`, `.telephone.type`, `.name.*` | `*EMAIL*`, `*PHONE*`, `*FIRST_NAME*`, etc. |

### 3. @Aggregation

| Annotation | Applied To | Values |
|---|---|---|
| `@Aggregation.default` | Measure elements in FACT entities | `SUM`, `MIN`, `MAX`, `AVG`, `COUNT_DISTINCT`, `NONE`, `FORMULA` |

### 4. @AnalyticsDetails

| Annotation | Applied To | Values |
|---|---|---|
| `@AnalyticsDetails.measureType` | Elements with `@Aggregation.default` | `BASE`, `RESTRICTION`, `CALCULATION` |

### 5. @ObjectModel

| Annotation | Applied To | Source |
|---|---|---|
| `.modelingPattern` | Entities | `ANALYTICAL_FACT`, `ANALYTICAL_DIMENSION`, `LANGUAGE_DEPENDENT_TEXT`, `VALUE_HELP_PROVIDER`, `DATA_STRUCTURE` |
| `.supportedCapabilities` | Entities | Array of capabilities; always includes `CDS_MODELING_DATA_SOURCE` + `CDS_MODELING_ASSOCIATION_TARGET` |
| `.semanticKey` | Entities | Single business-meaningful PK (column ending in Id/Code/Key/Number) |
| `.representativeKey` | Entities | First PK column for composite keys |
| `.foreignKey.association` | FK elements | Element Reference format `{"=": "_Product"}` per CSN Interop spec |
| `.tenantWideUniqueName` | Entities | `DATABASE.SCHEMA.TABLE_NAME` |
| `.usageType.sizeCategory` | Entities | `S`/`M`/`L`/`XL`/`XXL` from row counts |
| `.text.association` | Key elements | Element Reference `{"=": "_CompositionName"}` on the key element pointing to the text composition |
| `.text.element` | Key elements | Inline text reference (e.g., `["CustomerName"]`) |
| `.compositionRoot` | Parent entities | `true` when entity has compositions |

### 6. @PersonalData

Two-tier detection (masking policies + heuristics). All keys use **camelCase** per CSN Interop spec.

| Annotation | Applied To | Values |
|---|---|---|
| `.entitySemantics` | Entities with PII elements | `DATA_SUBJECT`, `DATA_SUBJECT_DETAILS`, `OTHER` |
| `.fieldSemantics` | PII elements | `DATA_SUBJECT_ID` (business user IDs), `DATA_SUBJECT_ID_TYPE` (email), `USER_ID` (system audit), `CONSENT_ID`, etc. |
| `.isPotentiallyPersonal` | PII elements | `true` — from masking policies, name patterns, or hash columns |
| `.isPotentiallySensitive` | Sensitive PII elements | `true` — email, phone, SSN, DOB, IP address |
| `.dataSubjectRole` | Entities (manual) | e.g., `"Customer"`, `"Employee"` |

### 7. @Consumption

| Annotation | Applied To | Source |
|---|---|---|
| `.valueHelpDefinition` | FK elements | Auto-linked to the `_<TargetEntity>` association |

### 8. @DataIntegration

| Annotation | Applied To | Source |
|---|---|---|
| `.dataUnavailable` | Elements mapped from VARIANT/OBJECT/ARRAY/GEOGRAPHY/GEOMETRY/VECTOR | Data not directly consumable as CDS types |

### 9. @ODM and @EntityRelationship (Manual Only)

These require SAP domain knowledge and are offered in Step 4:

| Vocabulary | Annotations | Requirement |
|---|---|---|
| **@ODM** | `.entityName`, `.oid`, `.oidReference.entityName` | SAP One Domain Model naming |
| **@EntityRelationship** | `.entityType`, `.propertyType`, `.references` | SAP VDM globally unique type URIs |

## Snowflake to CDS Type Mapping

| Snowflake Type | CDS Type | Properties |
|---|---|---|
| VARCHAR(n), CHAR(n) (n ≤ 5000) | `cds.String` | `length: n` |
| VARCHAR(n), CHAR(n) (n > 5000) | `cds.LargeString` | (none — CSN spec caps `cds.String.length` at 5000) |
| STRING, TEXT (Snowflake default 16777216) | `cds.LargeString` | (none) |
| VARCHAR with `*UUID*`/`*GUID*` name (length 36) | `cds.UUID` | (none) |
| NUMBER, DECIMAL, NUMERIC | `cds.Decimal` | `precision`, `scale` |
| INT, INTEGER, BIGINT, SMALLINT, TINYINT, BYTEINT | `cds.Decimal` | `precision: 38, scale: 0` (Snowflake stores all integer types as `NUMBER(38,0)` internally; SAP Datasphere sees `DECIMAL`. Using `cds.Integer` causes "MISMATCH IN DATA TYPE" import failures) |
| FLOAT, FLOAT4, FLOAT8, DOUBLE, REAL | `cds.Double` | |
| BOOLEAN | `cds.Boolean` | |
| DATE | `cds.Date` | |
| TIME | `cds.Time` | |
| TIMESTAMP, TIMESTAMP_NTZ, TIMESTAMP_LTZ, TIMESTAMP_TZ | `cds.Timestamp` | |
| BINARY, VARBINARY | `cds.Binary` | `length` |
| VARIANT, OBJECT, ARRAY | `cds.LargeString` | + `@DataIntegration.dataUnavailable: true` |
| GEOGRAPHY, GEOMETRY | `cds.LargeString` | + `@DataIntegration.dataUnavailable: true` |
| VECTOR | `cds.LargeString` | + `@DataIntegration.dataUnavailable: true` |

Unmapped types default to `cds.String`.

## Common Pitfalls

1. **Schema name case sensitivity**: Snowflake schema names are case-sensitive. If queries return 0 rows, the schema name is likely the wrong case. The skill uses `SHOW SCHEMAS IN DATABASE` to auto-detect the correct case, but if you specify a schema manually, ensure it matches exactly (e.g., lowercase `product` vs uppercase `PRODUCT`).

2. **Uniform VARCHAR lengths (16,777,216)**: Iceberg tables, catalog-linked databases, and Openflow-replicated tables report Snowflake's default VARCHAR max instead of meaningful business lengths. The skill detects this and prompts you to choose between mapping to `cds.LargeString` or applying SAP-typical default lengths. **Recommendation**: choose `cds.LargeString` for these sources — the CSN Interop schema caps `cds.String.length` at 5000, so values above that must use `cds.LargeString` regardless.

3. **Missing PK/FK constraints**: Many Snowflake sources (Iceberg, external tables, catalog-linked) don't have INFORMATION_SCHEMA constraint views. The skill falls back to heuristic association inference, which may produce false positives. Always review inferred associations in Step 3.

4. **Over-classified bridge tables**: In earlier versions, entities with only FK columns were incorrectly classified as `ANALYTICAL_DIMENSION`. The skill now detects bridge/link tables and classifies them as `DATA_STRUCTURE`.

5. **PascalCase word-boundary errors**: Greedy dictionary splitting of concatenated uppercase SAP column names is unreliable (e.g., `CREATEDBYUSER` can become `CrEaTeDbYuSeR`). The skill builds an explicit `KNOWN_COLUMNS` dictionary mapping each column name to its correct PascalCase form. Review generated names in Step 3.

6. **VARIANT/OBJECT/ARRAY columns**: These map to `cds.LargeString` since CSN Interop doesn't support nested types. The skill adds `@DataIntegration.dataUnavailable: true` to flag them. If the data is important, consider flattening these columns in a view before generating CSN.

7. **Composition missing `on` clause**: Both `cds.Association` and `cds.Composition` elements MUST include an `on` clause. Earlier versions omitted the `on` clause on `cds.Composition` elements (text table compositions), causing validation errors ("missing property on composition type"). The skill now always generates `on` clauses for compositions.

8. **@PersonalData format confusion**: The skill uses CSN Interop convention (camelCase keys: `entitySemantics`, `fieldSemantics`, enum notation `{"#": "VALUE"}`). This differs from CAP CDS convention (PascalCase: `EntitySemantics`, string values). If importing into a CAP project, you may need to convert.

9. **SAP CDS annotations in CSN Interop output**: SAP S/4HANA CDS exports (`.csn` files from S/4 or BTP CAP) contain a much larger annotation vocabulary than CSN Interop v1.2. Annotations like `@Analytics.dataCategory`, `@ObjectModel.sapObjectNodeType.name`, `@ObjectModel.usageType.dataClass/serviceQuality`, `@ObjectModel.dataCategory`, `@Semantics.systemDate.*`, `@Semantics.systemDateTime.*`, `@Semantics.user.*`, and `@Semantics.booleanIndicator` are **SAP CDS only** — they are not in any of the 10 CSN Interop extension vocabularies and will fail SAP CSN Interop validator checks. This skill generates only the 10 spec-compliant vocabularies. (`@Semantics.time` IS in the CSN Interop spec and IS generated for TIMS-style time columns.)

10. **Masking policy access**: `POLICY_REFERENCES` requires adequate privileges. If you lack access, PII detection relies entirely on column name heuristics, which may miss custom-named PII columns (e.g., `CUST_TAXREF` won't be flagged, but `TAX_ID` will).

## Troubleshooting

### Query returns 0 rows

**Cause**: Schema name case mismatch.
**Fix**: The skill runs `SHOW SCHEMAS IN DATABASE` to discover the exact schema name. If you specified a schema manually, check that it matches the casing shown by `SHOW SCHEMAS`.

### "KEY_COLUMN_USAGE view does not exist"

**Cause**: Catalog-linked databases, Iceberg tables, and some external table configurations don't provide constraint views.
**Fix**: This is expected. The skill falls back to heuristic association inference. Review inferred associations in Step 3/4.

### POLICY_REFERENCES fails

**Cause**: Insufficient privileges to query masking policies, or the table function syntax is wrong.
**Fix**: Ensure the `REF_ENTITY_DOMAIN` is `'TABLE'` (not `'SCHEMA'`). If you lack privileges, PII detection falls back to column name heuristics.

### Generated CSN has no associations

**Cause**: Both PK/FK queries failed AND the schema has no shared column name patterns that trigger heuristic inference.
**Fix**: Manually add associations in Step 4 by specifying source column → target entity/column relationships.

### All entities classified as DIMENSION

**Cause**: The schema has no clear FACT indicators (no measure-heavy entities, no transaction-name patterns).
**Fix**: Reclassify entities in Step 4. Mark entities with many numeric measure columns as FACT.

### Output file is too large

**Cause**: Large schemas with many tables produce verbose JSON due to per-element annotations.
**Fix**: Filter to specific tables using the table name filter in Step 2C, or generate CSN for subsets of the schema.

### Validation errors when importing to SAP

**Cause**: Missing required annotations or incorrect enum values.
**Fix**: Ensure `csnInteropEffective` is `"1.2"`, `$version` is `"2.0"`, all enums use `{"#": "VALUE"}` notation, and `@PersonalData` keys are camelCase. The skill optionally validates against the official CSN Interop JSON Schema in Step 3.

## Known Limitations

1. **No CDS equivalent types**: VARIANT/OBJECT/ARRAY/GEOGRAPHY/GEOMETRY/VECTOR map to `cds.LargeString` — CSN Interop has no array, nested object, or spatial types.
2. **Semantic View associations**: `DESCRIBE SEMANTIC VIEW` does not expose join relationships. Use raw tables mode or manually add associations.
3. **Heuristic associations**: Auto-inferred from shared column names; may produce false positives. Always reviewed in Step 4A.
4. **PascalCase splitting**: Greedy dictionary matching is unreliable for SAP concatenated names. The skill builds explicit `KNOWN_COLUMNS` + `ENTITY_MAP` dictionaries instead.
5. **String lengths**: Iceberg/catalog-linked/Openflow tables often report VARCHAR max (16,777,216). The skill detects this and offers alternatives.
6. **Custom types**: `kind: type` definitions are not auto-generated.
7. **@ODM / @EntityRelationship**: Require SAP-specific naming knowledge; manual in Step 4.
8. **@PersonalData format**: Uses CSN Interop convention (camelCase + enum), not CAP CDS convention.
9. **ROW_COUNT accuracy**: `INFORMATION_SCHEMA.TABLES.ROW_COUNT` may be approximate or NULL for external/Iceberg tables, causing `sizeCategory` to be omitted.

## FAQ

**Q: What's the difference between SAP CDS CSN and CSN Interop v1.2?**
A: SAP CDS CSN (used in S/4HANA and BTP CAP projects) supports a large annotation vocabulary including `@Analytics.*`, `@ObjectModel.sapObjectNodeType.name`, `@Semantics.systemDate.*`, etc. CSN Interop v1.2 is a constrained interoperability subset — it only allows annotations from exactly 10 extension vocabularies (`@Aggregation`, `@AnalyticsDetails`, `@Consumption`, `@DataIntegration`, `@EndUserText`, `@EntityRelationship`, `@ObjectModel`, `@ODM`, `@PersonalData`, `@Semantics`). This skill generates CSN Interop v1.2 only.


**Q: Does this skill require Python or external dependencies?**
A: No. The skill runs entirely through Cortex Code using SQL queries and inline JSON construction.

**Q: Can I generate CSN for a single table?**
A: Yes. In Mode 3, specify the table name when prompted. The skill will generate a context with one entity.

**Q: Does the skill detect PII even without Snowflake masking policies?**
A: Yes. The skill uses a two-tier approach: (1) masking policies from `POLICY_REFERENCES` for confirmed PII, and (2) column name heuristics (patterns like `*EMAIL*`, `*SSN*`, `*PHONE*`, `*HASH*`) for inferred PII.

**Q: What's the difference between `DATA_SUBJECT_ID` and `USER_ID` in @PersonalData.fieldSemantics?**
A: `DATA_SUBJECT_ID` is for business-facing user identifiers (e.g., `CustomerId`, `UserId`) that represent the data subject. `USER_ID` is for system audit columns (e.g., `CREATEDBY`, `CHANGEDBY`) that track which internal user performed an action.

**Q: Why are my bridge/join tables classified as DATA_STRUCTURE instead of DIMENSION?**
A: The skill detects entities that have only foreign key columns and no descriptive attributes. These are bridge/link tables, not true dimensions.

**Q: Can I use the output with SAP BTP CAP projects?**
A: The CSN Interop format is consumed by SAP BDC and Datasphere. For CAP projects, you may need to convert enum notation from `{"#": "VALUE"}` to string values and `@PersonalData` keys from camelCase to PascalCase.

**Q: What happens if INFORMATION_SCHEMA constraint queries fail?**
A: The skill falls back to heuristic association inference using shared column names, text table patterns, and group/classification patterns. A warning is included in the Step 3 summary.

**Q: How do I add @ODM or @EntityRelationship annotations?**
A: These require SAP domain knowledge (ODM entity names, VDM type URIs) and are offered as optional enhancements in Step 4. Provide the SAP-standard values when prompted.

**Q: What CSN Interop version does this produce?**
A: CSN Interop Effective v1.2 with `$version: "2.0"`. This is the current standard as of the [CSN Interop specification](https://sap.github.io/csn-interop-specification/).

**Q: Can I validate the output against the official JSON Schema?**
A: Yes. The skill optionally validates against `https://sap.github.io/csn-interop-specification/spec-v1/csn-interop-effective.schema.json` and extension schemas during Step 3.

**Q: How are associations named?**
A: Following SAP convention, associations use a `_` prefix (e.g., `_Product`, `_ProductGroup`), not a `to_` prefix. The ON clause uses `[association_target_field, =, source_field]` order per the CSN Interop spec (target navigation first, source second).

## File Structure

```
csn-generator/
├── INSTRUCTIONS.md                       # Cortex Code playbook (source of truth)
├── README.md                             # This file
├── outputs/                              # Generated CSN files (created during usage)
│   ├── product-from-semantic-view.csn.json  # Example: generated from Semantic View
│   ├── product.from-raw-tables.csn.json     # Example: generated from raw table metadata
│   └── sharepoint.csn.json              # Example: SharePoint schema
├── references/
│   ├── csn-construction-rules.md         # Detailed CSN construction rules
│   ├── csn-spec-reference.md             # CSN Interop quick reference
│   ├── snowflake-type-mapping.md         # Snowflake → CDS type mapping
│   ├── pascal-case-dictionary.md         # 200+ word SAP PascalCase dictionary
│   ├── annotation-patterns.md            # Column-name-to-annotation pattern tables
│   └── supported-features.md             # Complete feature support matrix (68 features)
└── sample-csn-files/                     # SAP S/4HANA CDS exports (SAP CDS CSN format — NOT CSN Interop; do NOT copy annotation patterns from these files)
    ├── product.json
    ├── customer.json
    ├── supplier.json
    ├── ledger.json
    ├── cashflow.json
    ├── profitcenter.json
    └── entryviewjournalentry.json
```

## References

- [CSN Interop Specification](https://sap.github.io/csn-interop-specification/)
- [CSN Interop Primer](https://sap.github.io/csn-interop-specification/primer)
- [CSN Interop Interface Documentation](https://sap.github.io/csn-interop-specification/spec-v1/csn-interop-effective)
- [Annotation Vocabularies](https://sap.github.io/csn-interop-specification/spec-v1/extensions)
- [Airline Example](https://sap.github.io/csn-interop-specification/spec-v1/examples/airline)
- [JSON Schema](https://sap.github.io/csn-interop-specification/spec-v1/csn-interop-effective.schema.json)
