# CSN Interop Supported Features

Complete feature support matrix for the SAP CSN Generator skill.

| CSN Feature | Support Level | Notes |
|---|---|---|
| Root structure (`csnInteropEffective`, `$version`, `definitions`) | Full | All mandatory fields |
| `meta` section (document metadata) | Full | title, version, doc, features, creator |
| Entity definitions (`kind: entity`) | Full | With elements, keys, notNull |
| Context definitions (`kind: context`) | Full | SAP BDC standard; entities prefixed with namespace name |
| Custom type definitions (`kind: type`) | Not generated | Can be manually added post-generation |
| `@EndUserText.label` | Full | Auto-generated from column names |
| `@EndUserText.heading` | Full | Auto-generated, uses comments when available |
| `@EndUserText.quickInfo` | Full | Uses column comments when available |
| `@ObjectModel.foreignKey.association` | Full | Element Reference format (e.g., `{"=": "_Product"}`) per CSN Interop spec; auto-detected from FK constraints or inferred |
| `@ObjectModel.modelingPattern` | Auto | Auto-inferred FACT/DIMENSION, refinable in Step 4 |
| `@ObjectModel.representativeKey` | Full | Auto-set for composite keys |
| `@ObjectModel.dataCategory` | **Not in CSN Interop spec** | SAP CDS only; use `@ObjectModel.modelingPattern: {"#": "LANGUAGE_DEPENDENT_TEXT"}` instead |
| `@ObjectModel.text.association` | Auto | Element Reference `{"=": "_CompositionName"}` on the **key element** (not on the composition, not boolean); auto-detected for text/description tables |
| `@ObjectModel.text.element` | Auto | Inline text reference for same-entity display names |
| `@ObjectModel.sapObjectNodeType.name` | **Not in CSN Interop spec** | SAP CDS only; do not generate |
| `@ObjectModel.usageType.dataClass` | **Not in CSN Interop spec** | SAP CDS only; do not generate |
| `@ObjectModel.usageType.serviceQuality` | **Not in CSN Interop spec** | SAP CDS only; do not generate |
| `@ObjectModel.semanticKey` | Auto | On entities with business-meaningful PK |
| `@ObjectModel.usageType.sizeCategory` | Auto | S/M/L/XL/XXL from Snowflake row counts |
| `@ObjectModel.tenantWideUniqueName` | Auto | Fully-qualified Snowflake table name |
| `@ObjectModel.compositionRoot` | Auto | On parent entities with compositions |
| `@ObjectModel.custom` | Manual | Mark custom elements/entities |
| `@Analytics.dataCategory` | **Not in CSN Interop spec** | SAP CDS only; `@Analytics` is not one of the 10 CSN Interop vocabularies |
| `@Aggregation.default` | Auto | SUM on measure columns in fact tables |
| `@AnalyticsDetails.measureType` | Auto | BASE on measure columns in FACT entities |
| `@Semantics.currencyCode` | Auto | Auto-detected from column name patterns |
| `@Semantics.unitOfMeasure` | Auto | Auto-detected from column name patterns |
| `@Semantics.amount.currencyCode` | Auto | Auto-linked amount columns to currency columns |
| `@Semantics.quantity.unitOfMeasure` | Auto | Auto-linked quantity columns to unit columns |
| `@Semantics.systemDate.*` | **Not in CSN Interop spec** | SAP CDS only; do not generate |
| `@Semantics.systemDateTime.*` | **Not in CSN Interop spec** | SAP CDS only; do not generate |
| `@Semantics.businessDate.*` | Auto | Auto-detected validity date columns |
| `@Semantics.language` | Auto | Auto-detected language columns |
| `@Semantics.user.*` | **Not in CSN Interop spec** | SAP CDS only; do not generate |
| `@Semantics.calendar.*` | Auto | year, quarter, month, week, dayOfMonth |
| `@Semantics.fiscal.*` | Auto | year, period, quarter, yearPeriod, yearVariant |
| `@Semantics.eMail.address` | Auto | Auto-detected email columns |
| `@Semantics.telephone.type` | Auto | WORK, FAX, CELL phone columns |
| `@Semantics.name.*` | Auto | givenName, familyName, fullName |
| `@Semantics.text` | Auto | On display-text columns (Name, Description, Text) |
| `@Semantics.booleanIndicator` | **Not in CSN Interop spec** | SAP CDS only; do not generate |
| `@Semantics.time` | Auto | Tags time-of-day fields (boolean: `true`); per CSN Interop spec `semantics.yaml`. Detect TIMS-style 6-char `cds.String` time columns (e.g., `START_TIME`, `*_HHMMSS`) |
| `@PersonalData.entitySemantics` | Auto | Enum: DATA_SUBJECT, DATA_SUBJECT_DETAILS, OTHER |
| `@PersonalData.fieldSemantics` | Auto | Enum: DATA_SUBJECT_ID, CONSENT_ID, etc. |
| `@PersonalData.isPotentiallyPersonal` | Auto | From masking policies + column name heuristics |
| `@PersonalData.isPotentiallySensitive` | Auto | Email, phone, SSN, DOB columns |
| `@PersonalData.dataSubjectRole` | Manual | Application-specific role string |
| `@PersonalData.dataSubjectRoleDescription` | Manual | Description of data subject role |
| `@Consumption.valueHelpDefinition` | Auto | On FK columns pointing to dimension entities |
| `@DataIntegration.dataUnavailable` | Auto | On VARIANT/OBJECT/ARRAY/GEOGRAPHY/VECTOR columns |
| `@ODM.entityName` | Manual | SAP One Domain Model entity name |
| `@ODM.oid` | Manual | OID element reference |
| `@ODM.oidReference.entityName` | Manual | OID reference on FK elements |
| `@EntityRelationship.entityType` | Manual | Cross-boundary entity type |
| `@EntityRelationship.propertyType` | Manual | Cross-boundary property type |
| `@EntityRelationship.references` | Manual | Cross-boundary entity references |
| `cds.UUID` type mapping | Auto | VARCHAR columns with UUID/GUID in name |
| Cardinality `min` | Auto | Set on notNull FK associations |
| `cds.Association` (foreign key) | Full | Auto-generated from FK constraints or inferred |
| `cds.Composition` (text) | Auto | Auto-generated for text table relationships |
| i18n localization | Full | Default: `{i18n>...}` placeholders + `"en"` translations; additional languages via Step 4 |
| `$schema` reference | Full | Points to official JSON Schema |
| CDC column filtering | Full | Openflow, Fivetran, Stitch, Airbyte patterns |
| Masking policy detection | Full | POLICY_REFERENCES for PII column discovery |
| Classification tag detection | Partial | TAG_REFERENCES checked, used when available |
