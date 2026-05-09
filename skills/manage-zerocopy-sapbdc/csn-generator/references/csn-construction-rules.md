# CSN Construction Rules

> ⚠️ **About the sample CSN files in `sample-csn-files/`** — These 7 files (`customer.json`, `product.json`, `supplier.json`, `ledger.json`, `cashflow.json`, `profitcenter.json`, `entryviewjournalentry.json`) are **SAP-originated CSN exports** (note the `__abapOriginalName` field, references to SAP ABAP table names like `KNA1`, and lowercase namespaces like `customer.Customer`). They demonstrate the **inbound** direction — SAP defines its own naming conventions when SAP is the source.
>
> **DO NOT copy these patterns verbatim when generating outbound CSN** (Snowflake → SAP BDC). Specifically:
> - Their lowercase namespaces (e.g., `customer`) violate the UPPERCASE rule for outbound publishing
> - Their `@ObjectModel.foreignKey.association` direct-string values violate the Element Reference rule
> - Their PascalCase entity names (e.g., `customer.Customer`) violate the "match Snowflake exactly" rule
>
> Use the samples as **reference for which annotations exist** (rich PII, semantic, analytics annotations) and **how to structure complex compositions** — but always apply this file's rules to the outbound CSN you generate. The sample files are kept as-is to preserve their value as annotation reference material.


Detailed rules for constructing CSN Interop v1.2 JSON documents from Snowflake metadata.

## Root Structure

Every CSN document MUST have this root structure. The `meta.creator` field is MANDATORY — it identifies the tool that generated the document and is required for traceability in SAP BDC:

```json
{
  "$schema": "https://sap.github.io/csn-interop-specification/spec-v1/csn-interop-effective.schema.json",
  "csnInteropEffective": "1.2",
  "$version": "2.0",
  "meta": {
    "document": {
      "version": "1.0.0",
      "title": "<DATABASE>.<SCHEMA> Data Model",
      "doc": "CSN Interop document generated from Snowflake <DATABASE>.<SCHEMA> metadata."
    },
    "creator": "Snowflake CSN Interop Generator (Cortex Code Skill) v10.0.0",
    "features": {
      "complete": true
    }
  },
  "definitions": {}
}
```

## Namespace Definition

Add one namespace definition as the first entry in `definitions`, using `kind: context` (SAP BDC standard):

```json
"<NAMESPACE_NAME>": {
  "kind": "context"
}
```
Use the **UPPERCASE Snowflake schema name** as the namespace, exactly as it appears in `INFORMATION_SCHEMA` (e.g., `"ECOMMERCE"`, `"SALES"`). This MUST match the actual schema path in Snowflake — SAP Datasphere resolves remote tables from CSN entity keys, and any case mismatch causes import to fail with **"Remote object could not be found"**.

## Entity Definitions

For each table/entity, add an entry in `definitions` with key `<NAMESPACE_NAME>.<EntityName>`:

```json
"<NAMESPACE_NAME>.<EntityName>": {
  "kind": "entity",
  "@EndUserText.label": "{i18n><EntityName>@ENDUSERTEXT.LABEL}",
  "elements": {}
}
```

If the table has a comment, add `"doc": "<comment>"`.

If the table has a composite primary key (more than one PK column), add:
```json
"@ObjectModel.representativeKey": "<FIRST_PK_COLUMN>"
```
Use the first PK column by ordinal position.

### Mandatory Entity-Level Annotations

Every entity definition MUST include ALL of the following annotations. These are not optional — omitting them results in incomplete CSN that SAP BDC tooling may reject or handle poorly:

| Annotation | Source | Notes |
|---|---|---|
| `@EndUserText.label` | Humanized entity name | Always present |
| `@ObjectModel.modelingPattern` | Auto-classified | See modelingPattern table below |
| `@ObjectModel.supportedCapabilities` | Auto-classified | Always includes `CDS_MODELING_DATA_SOURCE` + `CDS_MODELING_ASSOCIATION_TARGET` |
| `@ObjectModel.usageType.sizeCategory` | Row count from Query 6 | S/M/L/XL/XXL; omit only if row count unavailable |
| `@ObjectModel.tenantWideUniqueName` | Fully-qualified Snowflake name | Always present |

Do NOT skip any of these annotations. If metadata is insufficient to determine a value (e.g., row count is NULL), use a reasonable default or omit only that specific annotation with a warning in the Step 3 summary.

## Element Naming Convention

> ⚠️ **CRITICAL FOR BDC PUBLISHING**: Entity keys and element keys in the CSN **MUST be UPPERCASE matching Snowflake's actual schema/table/column names** (as returned by `INFORMATION_SCHEMA`). SAP Datasphere resolves remote table paths from these keys; any case mismatch causes import to fail with `"Remote object could not be found"`. The naming choice below ONLY affects the human-readable `@EndUserText.label` displayed in SAP UI — it does NOT change the entity/element keys.

**Ask** user which display-label convention to use (default: Original):

```
Which convention should be used for the human-readable display LABELS in the CSN?
(Note: entity and element KEYS will always be UPPERCASE matching Snowflake — this choice
only affects the @EndUserText.label values shown in SAP UI.)

1. Original (preserve source, default) — Labels match Snowflake names exactly (e.g.,
   PRODUCTEXTERNALID stays PRODUCTEXTERNALID). Best when consumers want exact
   traceability back to Snowflake source naming.
2. PascalCase (cosmetic SAP-style labels) — Labels are humanized to PascalCase
   (e.g., PRODUCTEXTERNALID → ProductExternalId). Best when SAP BDC consumers
   prefer human-friendly labels in their UI. Underlying keys stay UPPERCASE.
```

**STOP**: Wait for user selection.

| Option | Example Input | Entity Key | Element Key | `@EndUserText.label` |
|---|---|---|---|---|
| **Original** (preserve source) | `PRODUCTEXTERNALID` | `PRODUCTEXTERNALID` | `PRODUCTEXTERNALID` | `PRODUCTEXTERNALID` |
| **PascalCase** (label-only) | `PRODUCTEXTERNALID` | `PRODUCTEXTERNALID` | `PRODUCTEXTERNALID` | `ProductExternalId` |

**Note:** Both options produce the same entity/element keys. The difference is only in the cosmetic `@EndUserText.label` value. Never let PascalCase leak into entity or element keys when publishing to SAP BDC.

**PascalCase conversion rules:**
- If the source name is ALL_UPPERCASE with underscores (e.g., `ORDER_ID`), split on `_` and capitalize each word: `OrderId`
- If the source name is ALL_UPPERCASE without underscores (e.g., `PRODUCTEXTERNALID`), you MUST build an explicit column-name lookup dictionary from the query results. Do NOT rely on greedy dictionary splitting alone — it produces mangled names on SAP concatenated column names (e.g., `CREATEDBYUSER` → `CrEaTeDbYuSeR` instead of `CreatedByUser`).
- **Recommended approach**: After running Query 2, iterate through all column names and build a `KNOWN_COLUMNS` dictionary mapping each ALL_UPPERCASE name to its correct PascalCase form (e.g., `{"CREATEDBYUSER": "CreatedByUser", "ISMARKEDFORDELETION": "IsMarkedForDeletion", ...}`). Similarly, build an `ENTITY_MAP` dictionary for all table names. Use these dictionaries as the primary lookup, falling back to dictionary splitting only for names not in the map.
- For numbered column patterns (e.g., `TOTALCONSUMPTION1QUANTITY` through `TOTALCONSUMPTION13QUANTITY`), use regex to extract the prefix, number, and suffix, then PascalCase the prefix and suffix separately.
- See [pascal-case-dictionary.md](pascal-case-dictionary.md) for the 200+ word SAP dictionary. This dictionary is useful as a reference for building the explicit lookup, but greedy matching against it is unreliable for names containing short words like `AT`, `IN`, `OF`, `IS`, `TO`, `BY`, `NO`, `ON` which match greedily inside longer words.

- The `@EndUserText.label` always uses the humanized form regardless of element name choice

## Element Definitions

For each column (after CDC filtering), add an element inside the entity's `"elements"` object:

```json
"<ElementName>": {
  "type": "<CDS_TYPE>",
  "@EndUserText.label": "{i18n><EntityName>.<ElementName>@ENDUSERTEXT.LABEL}",
  "@EndUserText.heading": "{i18n><EntityName>.<ElementName>@ENDUSERTEXT.HEADING}",
  "@EndUserText.quickInfo": "{i18n><EntityName>.<ElementName>@ENDUSERTEXT.QUICKINFO}"
}
```

**i18n placeholder format** (matches SAP BDS CSN Aggregator convention):
- Entity label: `{i18n><EntityName>@ENDUSERTEXT.LABEL}`
- Element label: `{i18n><EntityName>.<ElementName>@ENDUSERTEXT.LABEL}`
- Element heading: `{i18n><EntityName>.<ElementName>@ENDUSERTEXT.HEADING}`
- Element quickInfo: `{i18n><EntityName>.<ElementName>@ENDUSERTEXT.QUICKINFO}`

Where `<EntityName>` is the PascalCase entity name WITHOUT the namespace prefix (e.g., `ProductPlant`, not `product.ProductPlant`), and `<ElementName>` is the PascalCase element name. Use ALLUPPERCASE for the element name in the i18n key if the source column is ALLUPPERCASE and Original naming is selected.

**i18n translations block**: Add a root-level `"i18n"` object to the CSN document containing the resolved text for each placeholder. At minimum, include `"en"` (English). The translations use the differentiation rules below.

```json
{
  "$schema": "...",
  "csnInteropEffective": "1.2",
  "i18n": {
    "en": {
      "<EntityName>@ENDUSERTEXT.LABEL": "<Humanized Entity Name>",
      "<EntityName>.<ElementName>@ENDUSERTEXT.LABEL": "<Humanized Column Name>",
      "<EntityName>.<ElementName>@ENDUSERTEXT.HEADING": "<Short Heading>",
      "<EntityName>.<ElementName>@ENDUSERTEXT.QUICKINFO": "<Longer Description>"
    }
  },
  "meta": { ... },
  "definitions": { ... }
}
```

**Text value differentiation rules** (for the i18n translation values):
- `LABEL` value: Always present. Humanized from column name (replace `_` with spaces, capitalize words). Max ~40 characters. Example: `"Product External ID"`
- `HEADING` value: Always present. Abbreviated column heading suitable for narrow table columns. Should be SHORTER than label — abbreviate common words (e.g., `"Prod. Ext. ID"`, `"Crcy"` for Currency, `"Qty"` for Quantity, `"Cat."` for Category, `"Desc."` for Description, `"No."` for Number). If label is already short (1-2 words), heading may equal label.
- `QUICKINFO` value: Present when the column has a Snowflake COMMENT or a Semantic View description. Use the comment/description text verbatim. If no comment exists, generate a slightly longer descriptive phrase than the label (e.g., for label `"Product External ID"`, quickInfo could be `"External identifier for the product in source system"`). The quickInfo should always be MORE descriptive than the label, never identical.

**IMPORTANT**: LABEL, HEADING, and QUICKINFO values must NOT all be identical strings. Each serves a different UI purpose (field label, column header, tooltip). If all three would be the same, differentiate HEADING (shorter) and QUICKINFO (longer/more descriptive).

Additional properties based on metadata:
- If the column is a primary key: add `"key": true` AND `"notNull": true"` (SAP convention: key elements are always non-nullable)
- If the column is NOT NULL (even if not a key): add `"notNull": true`
- If the column has a comment: add `"doc": "<comment>"`
- For type-specific arguments: add `"length"`, `"precision"`, `"scale"` per the type mapping

## String Length Handling

When mapping TEXT/VARCHAR columns, check if the source has meaningful lengths:

> ⚠️ **CSN Interop length cap**: The CSN Interop schema caps `cds.String.length` at **5000** (per `CSN-Interop-Effective.schema.yaml`: `length.maximum: 5000`). Any column whose length exceeds 5000 MUST be mapped to `cds.LargeString` (no length property), not `cds.String` with an oversized length. JSON Schema-validating SAP tooling will reject CSNs that violate this cap.

- If **all** TEXT columns in the schema share the same CHARACTER_MAXIMUM_LENGTH (e.g., `16777216` or `134217728`), this indicates the source has no explicit length constraints (common with Iceberg tables, catalog-linked databases, and Openflow-replicated tables). In this case:
  - **Ask** user which approach to use:
    - **Use `cds.LargeString`** (recommended for Iceberg/external sources): Map TEXT columns to `"type": "cds.LargeString"` with no length property. Best for unstructured or arbitrarily long text.
    - **Use SAP defaults**: Apply SAP-typical lengths (e.g., 40 for product IDs, 256 for descriptions) based on column name heuristics. Keeps `cds.String` with a spec-valid length ≤ 5000.
- If TEXT columns have **varying** CHARACTER_MAXIMUM_LENGTH values:
  - For each column, if `CHARACTER_MAXIMUM_LENGTH` ≤ 5000, use `cds.String` with that length.
  - If `CHARACTER_MAXIMUM_LENGTH` > 5000, use `cds.LargeString` (omit length).

## Foreign Key Associations

For each detected foreign key relationship, add TWO things:

1. On the source column element, add the annotation:
```json
"@ObjectModel.foreignKey.association": { "=": "_<TargetEntity>" }
```

**IMPORTANT**: Use the **Element Reference format** `{ "=": "_Product" }`, NOT the direct string format `"_Product"`. Per the CSN Interop specification (see `object-model.yaml`, `x-ref-to-doc: ElementReference`), this annotation expects an Element Reference object — strict CSN parsers may reject the plain-string form. The Element Reference form is also consistent with how other association references (e.g., `@ObjectModel.text.association`, `@Consumption.valueHelpDefinition.association`) are written elsewhere in this file.

2. Add a new association element in the same entity:
```json
"_<TargetEntity>": {
  "type": "cds.Association",
  "target": "<NAMESPACE_NAME>.<TargetEntity>",
  "cardinality": { "max": 1 },
  "on": [
    { "ref": ["_<TargetEntity>", "<TARGET_COLUMN>"] },
    "=",
    { "ref": ["<SOURCE_COLUMN>"] }
  ]
}
```

**Cardinality `min` rule**: If the source FK column has `notNull: true` (i.e., IS_NULLABLE = 'NO'), set `"cardinality": { "min": 1, "max": 1 }` instead of just `{ "max": 1 }`. This indicates the association is mandatory (every row must reference a target). For to-many reverse associations on notNull columns, use `{ "min": 1, "max": "*" }`. If the FK column is nullable, omit `min` (only set `max`).

Note: The `on` clause MUST use `[association_target_field, =, source_field]` order per the CSN Interop specification. The left side navigates through the association to the target field (`{"ref": ["_<TargetEntity>", "<TARGET_COLUMN>"]}`), and the right side is the source entity's own field (`{"ref": ["<SOURCE_COLUMN>"]}`). Reversing this direction (source-first) is a spec violation and may cause association resolution failures in SAP Datasphere.

> **🚧 VERIFY**: This direction was validated against SAP Datasphere import in Kevin Poskitt's KP v2 testing (May 7, 2026, ECOMMERCE_TEST.ECOMMERCE 5-table dataset). Per Kevin's review (May 8, 2026): *"Validate against actual SAP BDC imports. If both directions work, document clearly. If only one works, standardize."* If a future test confirms both directions are accepted, simplify this note.

Association names MUST use `_` prefix (SAP convention, e.g., `_Product`, `_Plant`), NEVER `to_` prefix (e.g., `to_product`). The `_` prefix is a firm SAP CDS standard for managed associations.

## Auto-Inferred Semantic Annotations

Apply these annotations automatically based on column name patterns during initial construction (Step 2), not deferred to Step 4.

See [annotation-patterns.md](annotation-patterns.md) for all column-name-to-annotation pattern tables including:
- **@Semantics.businessDate**: Validity date ranges (businessDate.from / businessDate.to)
- **@Semantics.currencyCode / unitOfMeasure**: Auto-detect currency and unit columns, auto-link amounts to currencies and quantities to units
- **@Semantics.language**: Language code columns
- **@Semantics.text**: Display text columns (Name, Description, Text, Title, Label)
- **@Semantics.calendar.* / fiscal.***: Calendar year/quarter/month/week, fiscal year/period
- **@Semantics.eMail / telephone / name**: Contact-related columns
- **@PersonalData**: PII detection from masking policies + column name heuristics

### @ObjectModel.text.element (Inline Text Reference)

> 🔴 **CRITICAL FORMAT — DO NOT GET THIS WRONG**: `@ObjectModel.text.element` takes a **PLAIN STRING ARRAY**, NOT Element Reference format. This is the opposite of `@ObjectModel.foreignKey.association` and `@ObjectModel.text.association` (which DO use Element Reference). Using the wrong format here crashes the SAP Datasphere parser at import time.
>
> ✅ **CORRECT**: `"@ObjectModel.text.element": ["CUSTOMER_NAME"]`
> ❌ **WRONG**: `"@ObjectModel.text.element": [{"=": "CUSTOMER_NAME"}]`
>
> The wrong form will crash the SAP Datasphere parser with:
> ```
> Cannot read properties of undefined (reading 'name')
> ```
>
> **Format summary for the 4 sibling `@ObjectModel.*` annotations** (memorize this — it's the most common bug):
>
> | Annotation | Format | Example |
> |---|---|---|
> | `@ObjectModel.text.element` | **Plain string array** | `[ "COLUMN_NAME" ]` |
> | `@ObjectModel.text.association` | Element Reference | `{ "=": "_Composition" }` |
> | `@ObjectModel.semanticKey` | Element Reference array | `[ { "=": "KEY_COLUMN" } ]` |
> | `@ObjectModel.foreignKey.association` | Element Reference | `{ "=": "_Assoc" }` |
>
> Three of these use Element Reference, but `text.element` is the **exception** that uses plain strings. Confirmed in production by Kevin Poskitt (May 8, 2026, 3 of 3 SAP Datasphere import tests crashed when this rule was violated).

When an entity has both a key/ID column and a display-text column in the same entity (no separate text table), add `@ObjectModel.text.element` on the key column pointing to the text column:

```json
"Customer": {
  "key": true, "type": "cds.String", "length": 10,
  "@ObjectModel.text.element": ["CustomerName"]
}
```

Heuristic: For each key or semantic-key column `<X>` (e.g., `Customer`, `ProductId`, `Ledger`), look for a sibling element named `<X>Name`, `<X>Description`, or `<X>Text` that has `@Semantics.text: true`. If found, add `@ObjectModel.text.element: ["<SiblingName>"]` to the key column. This pattern is used instead of `@ObjectModel.text.association` when the text lives in the same entity rather than a separate text table.

**IMPORTANT**: Also check key fields that have a `cds.Composition` association to a text table (via `@ObjectModel.text.association` on the key element). If the key field's entity also contains a descriptive text element (e.g., `ProductGroupName` in the same entity as `ProductGroup`), the key field MUST still have `@ObjectModel.text.element` pointing to that local text element. The `@ObjectModel.text.association` (Element Reference `{"=": "_CompositionName"}` pointing to the text composition) and `@ObjectModel.text.element` (pointing to the local descriptive field) serve complementary purposes — the former provides language-dependent text via the composition, the latter provides a quick inline display name. Both annotations go on the **key element**.

### @PersonalData Annotations (PII Detection)

See [annotation-patterns.md](annotation-patterns.md#personaldata-annotations-pii-detection) for the full PII detection pattern table, masking policy rules, fieldSemantics enum values, and entity-level annotation rules.

Key operational rules:
- **camelCase** keys: `entitySemantics`, `fieldSemantics`, `isPotentiallyPersonal`, `isPotentiallySensitive`
- **Enum notation** `{"#": "VALUE"}` for `entitySemantics` and `fieldSemantics`
- If ANY element has `@PersonalData`, add entity-level `@PersonalData.entitySemantics`
- Distinguish `USER_ID` (system audit) vs `DATA_SUBJECT_ID` (business-facing)

### @AnalyticsDetails.measureType

For measure columns in FACT/CUBE entities (those with `@Aggregation.default`), add:
```json
"@AnalyticsDetails.measureType": {"#": "BASE"}
```
Values: `BASE` (standard measure from provider), `RESTRICTION` (restricted measure), `CALCULATION` (calculated/formula measure). Default to `BASE` for auto-inferred measures.

### @ObjectModel.modelingPattern and @ObjectModel.supportedCapabilities

| Entity Classification | modelingPattern | supportedCapabilities |
|---|---|---|
| FACT table | `{"#": "ANALYTICAL_FACT"}` | `[{"#": "ANALYTICAL_PROVIDER"}, {"#": "SQL_DATA_SOURCE"}, {"#": "CDS_MODELING_DATA_SOURCE"}, {"#": "CDS_MODELING_ASSOCIATION_TARGET"}]` |
| DIMENSION table | `{"#": "ANALYTICAL_DIMENSION"}` | `[{"#": "ANALYTICAL_DIMENSION"}, {"#": "SQL_DATA_SOURCE"}, {"#": "CDS_MODELING_DATA_SOURCE"}, {"#": "CDS_MODELING_ASSOCIATION_TARGET"}]` |
| Text table | `{"#": "LANGUAGE_DEPENDENT_TEXT"}` | `[{"#": "LANGUAGE_DEPENDENT_TEXT"}, {"#": "SQL_DATA_SOURCE"}, {"#": "CDS_MODELING_DATA_SOURCE"}, {"#": "CDS_MODELING_ASSOCIATION_TARGET"}]` |
| Value help entity | `{"#": "VALUE_HELP_PROVIDER"}` | `[{"#": "VALUE_HELP_PROVIDER"}, {"#": "SQL_DATA_SOURCE"}, {"#": "CDS_MODELING_DATA_SOURCE"}, {"#": "CDS_MODELING_ASSOCIATION_TARGET"}]` |
| General data structure | `{"#": "DATA_STRUCTURE"}` | `[{"#": "SQL_DATA_SOURCE"}, {"#": "CDS_MODELING_DATA_SOURCE"}, {"#": "CDS_MODELING_ASSOCIATION_TARGET"}]` |

**Note on supportedCapabilities**: Every entity that is a valid SQL data source MUST include `CDS_MODELING_DATA_SOURCE` and `CDS_MODELING_ASSOCIATION_TARGET` in its `supportedCapabilities` array. These indicate the entity can be used as a data source in CDS views and as an association target. Omitting them limits downstream consumption in SAP BTP tooling.

### @ObjectModel.semanticKey

If an entity has a single-column primary key that represents the business key (not a surrogate), add:
```json
"@ObjectModel.semanticKey": [{"=": "<KeyElementName>"}]
```
Heuristic: Apply when ANY of these conditions are met:
- PK column name matches the entity name pattern (e.g., entity `Product` with key `ProductId`)
- PK column name ends with `Id`, `ID`, `Code`, `Key`, `Number` and the entity has no other key columns
- Entity has a `notNull` column whose name is `<EntityName>Id` or `<EntityName>_ID` even without formal PK constraints

For heuristically-inferred keys (when PK/FK queries fail), also apply `@ObjectModel.semanticKey` on the column used as the join key in inferred associations, since it's the de facto business key.

### @ObjectModel.usageType.sizeCategory

Estimate entity size category based on Snowflake row counts from Query 6:

| Row Count | sizeCategory |
|---|---|
| < 1,000 | `{"#": "S"}` |
| < 100,000 | `{"#": "M"}` |
| < 10,000,000 | `{"#": "L"}` |
| < 100,000,000 | `{"#": "XL"}` |
| >= 100,000,000 | `{"#": "XXL"}` |

### @ObjectModel.tenantWideUniqueName

For all entities, set to the fully-qualified Snowflake table name:
```json
"@ObjectModel.tenantWideUniqueName": "<DATABASE>.<SCHEMA>.<TABLE_NAME>"
```

### @ObjectModel.compositionRoot

For entities that serve as root of a compositional hierarchy (parent entities with `cds.Composition` to child entities), add:
```json
"@ObjectModel.compositionRoot": true
```

**IMPORTANT**: Text tables (entities with `@ObjectModel.modelingPattern: {"#": "LANGUAGE_DEPENDENT_TEXT"}`) must NEVER have `@ObjectModel.compositionRoot: true`. Text tables are composition *targets* (children), not roots. Only true root parent entities that *own* compositions should be marked as composition roots. Example: `Product` is a composition root; `ProductDescription` (text table) is NOT.

**IMPORTANT**: Every `cds.Composition` element MUST include an `"on"` clause, exactly like `cds.Association` elements. The `on` clause links the parent entity's key column to the child entity's matching column through the composition navigation path. Omitting the `on` clause causes CSN validation errors ("missing property on composition type"). Example:
```json
"_ProductDescription": {
  "type": "cds.Composition",
  "target": "product.ProductDescription",
  "cardinality": { "max": "*" },
  "on": [{ "ref": ["_ProductDescription", "Product"] }, "=", { "ref": ["Product"] }]
}
```

### @Consumption.valueHelpDefinition

For FK columns that point to dimension/lookup entities, auto-generate value help:
```json
"@Consumption.valueHelpDefinition": [{
  "association": {"=": "_<TargetEntity>"}
}]
```
This links the FK column to the association that provides the value help.

### @DataIntegration.dataUnavailable

Add `@DataIntegration.dataUnavailable: true` on elements mapped from Snowflake `VARIANT`, `OBJECT`, `ARRAY`, `GEOGRAPHY`, `GEOMETRY`, or `VECTOR` types, since their data is not directly consumable as CDS-typed values.

### @ODM Annotations (Manual Step 4 Only)

These require SAP One Domain Model knowledge and are offered in Step 4:
- `@ODM.entityName`: Official ODM entity name (string, e.g., `"sap.odm.finance.CostCenter"`)
- `@ODM.oid`: Reference to the element containing the OID
- `@ODM.oidReference.entityName`: On FK elements that reference an ODM entity

### @EntityRelationship Annotations (Manual Step 4 Only)

These are for cross-service/cross-boundary relationships and require knowledge of SAP entity/property types:
- `@EntityRelationship.entityType`: Globally unique entity type (e.g., `"sap.vdm.sont:SalesOrder"`)
- `@EntityRelationship.propertyType`: Globally unique property type (e.g., `"sap.vdm.gfn:SalesOrderID"`)
- `@EntityRelationship.references`: Array of cross-boundary references to other entities
- `@EntityRelationship.temporalIds` / `@EntityRelationship.temporalReferences`: For time-dependent entity references

### @Semantics.calendar.* and @Semantics.fiscal.*

See [annotation-patterns.md](annotation-patterns.md#semanticscalendar-and-semanticsfiscal) for the full calendar/fiscal column pattern table.

### @Semantics Contact Annotations

See [annotation-patterns.md](annotation-patterns.md#semantics-contact-annotations) for the full contact column pattern table.

## Auto-Inferred Associations (when PK/FK queries fail)

When INFORMATION_SCHEMA constraint queries fail (common with Iceberg/external tables), infer associations heuristically:

**Association cardinality rules:**
- Use `"max": 1` (to-one) when the source column references a column that is the primary key or the sole `notNull` unique-looking column of the target entity. This is the common case for FK lookups.
- Use `"max": "*"` (to-many) only when creating reverse/navigational associations (e.g., from a parent entity back to its children) or when the target column is clearly not unique.
- **Default**: When uncertain, prefer `"max": 1` for forward FK associations.

1. **Shared key column pattern**: If multiple entities share a column with the same name (e.g., `PRODUCT` appears in 25 of 27 tables), and one entity is named after that column (e.g., entity `product`), create `cds.Association` from each child entity to the parent:
   - Identify the "master" entity: entity whose name matches the shared column name (case-insensitive)
   - For every other entity that has that column, add an association `_<MasterEntity>` targeting `<NAMESPACE_NAME>.<MasterEntity>` with `"max": 1`

2. **Text table pattern**: Entities whose name ends with `description`, `text`, `texts`, or `_text` are text tables. Auto-detect by checking if:
   - The entity has a `LANGUAGE` column
   - The entity has a column matching the parent entity's key (e.g., `PRODUCT`)
   - If both conditions met, add:
     - `@ObjectModel.modelingPattern: {"#": "LANGUAGE_DEPENDENT_TEXT"}` on the text entity
     - `@Semantics.language: true` on the `LANGUAGE` column
     - `@ObjectModel.text.association` on the **parent entity's key element** (NOT on the composition element) pointing to the composition element name, using Element Reference format: `{"=": "_<TextEntity>"}`.
     - A `cds.Composition` on the parent entity pointing to the text entity. **CRITICAL**: The composition element MUST include an `"on"` clause. Example:
       ```json
       "Product": {
         "key": true, "notNull": true, "type": "cds.String",
         "@ObjectModel.text.association": { "=": "_ProductDescription" }
       },
       "_ProductDescription": {
         "type": "cds.Composition",
         "target": "product.ProductDescription",
         "cardinality": { "max": "*" },
         "on": [
           { "ref": ["_ProductDescription", "Product"] }, "=",
           { "ref": ["Product"] }
         ]
       }
       ```

3. **Group/classification pattern**: Entities like `productgroup`, `productmrparea` that have a distinct key column (e.g., `PRODUCTGROUP`, `MRPAREA`) — if other entities reference that column, create an association.

## Auto-Inferred Analytical Annotations

Classify entities as FACT or DIMENSION automatically:

**ANALYTICAL_FACT** heuristics (entity is a fact table if ANY of these are true):
- Entity name contains `consumption`, `transaction`, `journal`, `entry`, `posting`, `cashflow`, `ledger`
- Entity has 10+ numeric (cds.Decimal) columns that look like measures (names containing `QUANTITY`, `AMOUNT`, `PRICE`, `COST`, `CONSUMPTION`)
- Entity has both a `CURRENCY` column AND multiple numeric columns with `INCOCODECRCY` in their names

**Bridge/Link table** heuristics (entity is a bridge table — use `DATA_STRUCTURE` instead of `ANALYTICAL_DIMENSION`):
- Entity has ONLY foreign key columns (all non-key columns are FK references to other entities) and no descriptive string columns
- Entity name contains `link`, `map`, `mapping`, `bridge`, `xref`, `rel`, or entity name joins two other entity names
- Entity has exactly 2-3 columns and all are ID/key columns pointing to other entities
- For bridge tables, set `@ObjectModel.modelingPattern: {"#": "DATA_STRUCTURE"}` and `@ObjectModel.supportedCapabilities: [{"#": "SQL_DATA_SOURCE"}]`

**ANALYTICAL_DIMENSION** heuristics (entity is a dimension if ANY of these are true AND it is NOT a bridge table):
- Entity name matches a master data pattern: `product`, `customer`, `supplier`, `plant`, `company`, `country`, `region`
- Entity has a `LANGUAGE` column (text/description table)
- Entity has few numeric columns relative to string columns (< 30% numeric)
- Entity has descriptive string columns beyond just IDs (e.g., `Name`, `Description`, `Status`, `Type`)
- Entity is referenced as an association target by multiple other entities

Apply:
- `@ObjectModel.modelingPattern` per the table above. **Every entity MUST have `@ObjectModel.modelingPattern` set.** Default to `{"#": "ANALYTICAL_DIMENSION"}` for master data/reference entities if not clearly a FACT. Text tables use `{"#": "LANGUAGE_DEPENDENT_TEXT"}`.
- `@ObjectModel.supportedCapabilities` per the table above
- For FACT entities, add `@Aggregation.default: {"#": "SUM"}` on numeric measure columns (those with `QUANTITY`, `AMOUNT`, `PRICE`, `COST`, `CONSUMPTION`, `WEIGHT`, `VOLUME` in the name). Other valid enum values: `NONE`, `MIN`, `MAX`, `AVG`, `COUNT_DISTINCT`, `NOP`, `FORMULA`. Do NOT add aggregation defaults on:
  - Percentage columns (`*PERCENT*`, `*TOLERANCE*`) — use `{"#": "AVG"}` or `{"#": "FORMULA"}` if needed
  - ID/code columns that happen to be numeric — use `{"#": "NONE"}`
  - Duration/period columns that are counts, not measures

## Snowflake to CDS Type Mapping

See [snowflake-type-mapping.md](snowflake-type-mapping.md) for the complete type mapping table.

**Key rules:**
- Strip parenthesized portions from types like `VARCHAR(100)` to get the base type, but use extracted values for `length`/`precision`/`scale`
- If a Snowflake type is not in the mapping table, default to `cds.String`
- VARIANT/OBJECT/ARRAY/GEOGRAPHY/GEOMETRY/VECTOR → `cds.LargeString` with explanatory `"doc"` annotation

**UUID override**: VARCHAR/CHAR columns whose name contains `UUID` or `GUID` and whose `CHARACTER_MAXIMUM_LENGTH` is 36 (or length is omitted) should be mapped to `cds.UUID` instead of `cds.String`. Examples: `ProductUUID`, `TrdClassfctnNmbrUUID`, `RequestGUID`. Do not set a `length` property on `cds.UUID`.
