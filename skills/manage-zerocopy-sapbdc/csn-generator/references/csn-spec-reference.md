# CSN Interop v1.2 Quick Reference

Spec URL: https://sap.github.io/csn-interop-specification/

> **Note**: This file shows generic CSN Interop spec patterns verbatim. The SAP BDC CSN Generator skill follows the spec for ON clause direction (`[target, =, source]`) and `@ObjectModel.foreignKey.association` format (Element Reference `{"=": "..."}`). The only deliberate deviation is:
> 1. **Association prefix**: SAP BDC uses `_` prefix (e.g., `_Product`) instead of the generic `to_` prefix (e.g., `to_Country`) shown in some spec examples.
>
> Earlier internal documentation claimed SAP BDC reversed the ON clause direction to `[source, =, target]` and used direct-string format for `@ObjectModel.foreignKey.association`. **Both claims have been corrected** — production testing by Kevin Poskitt (May 7-8, 2026) against SAP Datasphere imports confirmed the spec-correct forms work and the previous "SAP BDC convention" claims were incorrect.
>
> Always follow the conventions in `csn-construction-rules.md` when generating CSN output.

## Root Structure (Mandatory)

```json
{
  "$schema": "https://sap.github.io/csn-interop-specification/spec-v1/csn-interop-effective.schema.json",
  "csnInteropEffective": "1.2",
  "$version": "2.0",
  "meta": {
    "document": {
      "version": "1.0.0",
      "title": "My Data Model",
      "doc": "Description of the document"
    },
    "features": { "complete": true }
  },
  "definitions": { }
}
```

## Definition Kinds

| Kind | Purpose |
|------|---------|
| `entity` | A data table/view with elements (columns) |
| `service` | Groups entities exposed via API; entities use service name as prefix |
| `context` | Namespace grouping for unique names |
| `type` | Custom reusable type definition |

## Entity Pattern

```json
"ServiceName.EntityName": {
  "kind": "entity",
  "@EndUserText.label": "Human Label",
  "elements": {
    "ID": { "key": true, "type": "cds.String", "length": 36, "notNull": true },
    "Name": { "type": "cds.String", "length": 100 }
  }
}
```

## CDS Built-in Types

| Type | Arguments | Example |
|------|-----------|---------|
| cds.Boolean | - | `{"type": "cds.Boolean"}` |
| cds.String | length | `{"type": "cds.String", "length": 40}` |
| cds.LargeString | - | `{"type": "cds.LargeString"}` |
| cds.Integer | - | `{"type": "cds.Integer"}` |
| cds.Integer64 | - | `{"type": "cds.Integer64"}` |
| cds.Decimal | precision, scale | `{"type": "cds.Decimal", "precision": 16, "scale": 3}` |
| cds.Double | - | `{"type": "cds.Double"}` |
| cds.Date | - | `{"type": "cds.Date"}` |
| cds.Time | - | `{"type": "cds.Time"}` |
| cds.DateTime | - | `{"type": "cds.DateTime"}` |
| cds.Timestamp | - | `{"type": "cds.Timestamp"}` |
| cds.UUID | - | `{"type": "cds.UUID"}` |
| cds.Binary | length | `{"type": "cds.Binary", "length": 255}` |
| cds.LargeBinary | - | `{"type": "cds.LargeBinary"}` |

## Foreign Key Association Pattern

```json
"CountryCode": {
  "type": "cds.String", "length": 3,
  "@ObjectModel.foreignKey.association": { "=": "to_Country" }
},
"to_Country": {
  "type": "cds.Association",
  "target": "ServiceName.Country",
  "cardinality": { "max": 1 },
  "on": [
    { "ref": ["to_Country", "code"] },
    "=",
    { "ref": ["CountryCode"] }
  ]
}
```

## Text Association Pattern

```json
"code": {
  "key": true, "type": "cds.String", "length": 3,
  "@ObjectModel.text.association": { "=": "texts" }
},
"texts": {
  "type": "cds.Composition",
  "cardinality": { "max": "*" },
  "target": "ServiceName.Country_texts",
  "on": [
    { "ref": ["texts", "code"] }, "=", { "ref": ["code"] }
  ]
}
```

## Key Annotations

| Annotation | Scope | Purpose |
|-----------|-------|---------|
| `@EndUserText.label` | Entity, Element | Human-readable label |
| `@EndUserText.quickInfo` | Entity, Element | Tooltip/description |
| `@Aggregation.default` | Element | Default aggregation: `{"#": "SUM"}` |
| `@ObjectModel.modelingPattern` | Entity | `ANALYTICAL_FACT`, `ANALYTICAL_DIMENSION`, etc. |
| `@ObjectModel.foreignKey.association` | Element | Links to FK association |
| `@ObjectModel.text.element` | Element | Element(s) containing text for ID |
| `@ObjectModel.text.association` | Element | Association to text entity |
| `@Semantics.currencyCode` | Element | Element is a currency code |
| `@Semantics.amount.currencyCode` | Element | Links amount to currency element |
| `@Semantics.unitOfMeasure` | Element | Element is a unit of measure |
| `@Semantics.quantity.unitOfMeasure` | Element | Links quantity to unit element |
| `@Semantics.text` | Element | Element contains display text |
| `@Semantics.language` | Element | Element is a language code |
