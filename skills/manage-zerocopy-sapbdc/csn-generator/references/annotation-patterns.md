# Auto-Inferred Annotation Patterns

Column-name-to-annotation pattern tables for the SAP CSN Generator skill. These patterns are applied automatically during CSN construction based on column name matching (case-insensitive).

> **CRITICAL: Only CSN Interop v1.2 spec annotations are listed here.** Annotations from SAP CDS CSN (`@Semantics.systemDate.*`, `@Semantics.systemDateTime.*`, `@Semantics.user.*`, `@Semantics.booleanIndicator`) are **NOT** in the CSN Interop spec and must never be generated. (Note: `@Semantics.time` IS in the CSN Interop spec — see `semantics.yaml` — and is supported by this skill for TIMS-style time columns.)

## Business Date Annotations

| Column Name Pattern | Annotation |
|---|---|
| `VALIDITYSTARTDATE`, `VALID_FROM`, `VALIDFROM` | `@Semantics.businessDate.from: true` |
| `VALIDITYENDDATE`, `VALID_TO`, `VALIDTO` | `@Semantics.businessDate.to: true` |

## Currency and Unit Annotations

### Auto-detect currency code columns (`@Semantics.currencyCode: true`)
Column name is exactly `CURRENCY`, `CURRENCYCODE`, `CURRENCY_CODE`, `TRANSACTIONCURRENCY`, `COMPANYCODECRCY`, or ends with `_CURRENCY`

### Auto-detect unit of measure columns (`@Semantics.unitOfMeasure: true`)
- Column name is exactly or ends with: `BASEUNIT`, `UNIT`, `WEIGHTUNIT`, `VOLUMEUNIT`, `CONTENTUNIT`, `QUANTITYUNIT`
- Column name ends with `UNIT` and a sibling column exists that is a numeric measure

### Auto-link amounts to currency (`@Semantics.amount.currencyCode`)
- Numeric columns whose name contains `PRICE`, `AMOUNT`, `COST`, `MARGIN` and a `CURRENCY` column exists in the same entity
- Numeric columns whose name ends with `INCOCODECRCY` (SAP naming for "in company code currency") and `CURRENCY` exists

### Auto-link quantities to unit (`@Semantics.quantity.unitOfMeasure`)
Numeric columns whose name contains `QUANTITY`, `QTY`, `WEIGHT`, `VOLUME`, `CONTENT`, `CONSUMPTION` and a matching unit column exists:
- `*WEIGHT*` → link to `WEIGHTUNIT` if present, else `BASEUNIT`
- `*VOLUME*` → link to `VOLUMEUNIT` if present, else `BASEUNIT`
- `*CONTENT*` → link to `CONTENTUNIT` if present, else `BASEUNIT`
- `*QUANTITY*` or `*QTY*` → link to `BASEUNIT` if present
- `*CONSUMPTION*` → link to `BASEUNIT` if present

## Language Annotations

Column named `LANGUAGE` or `LANGUAGECODE`: add `@Semantics.language: true`

## Text Indicator Annotations

Add `@Semantics.text: true` on columns that carry human-readable display text. Heuristic: column name ends with `NAME`, `DESCRIPTION`, `TEXT`, `TITLE`, or `LABEL` and the column type is `cds.String`. Examples: `CustomerName`, `LedgerName`, `ProductDescription`, `MaterialText`.

Do NOT apply to ID/code columns even if they contain "name" in a compound context (e.g., `UserName` that is an ID). Use judgment: if the entity also has a separate ID column (e.g., `Customer` + `CustomerName`), the `*Name` column is the display text.

## @Semantics.calendar.* and @Semantics.fiscal.*

| Column Name Pattern | Annotation |
|---|---|
| `*CALENDAR_YEAR*`, `*CALYEAR*` | `@Semantics.calendar.year: true` |
| `*CALENDAR_QUARTER*`, `*CALQUARTER*` | `@Semantics.calendar.quarter: true` |
| `*CALENDAR_MONTH*`, `*CALMONTH*` | `@Semantics.calendar.month: true` |
| `*CALENDAR_WEEK*`, `*CALWEEK*` | `@Semantics.calendar.week: true` |
| `*DAY_OF_MONTH*` | `@Semantics.calendar.dayOfMonth: true` |
| `*FISCAL_YEAR*`, `*FISCALYEAR*` | `@Semantics.fiscal.year: true` |
| `*FISCAL_PERIOD*`, `*FISCALPERIOD*` | `@Semantics.fiscal.period: true` |
| `*FISCAL_QUARTER*` | `@Semantics.fiscal.quarter: true` |
| `*FISCAL_YEAR_PERIOD*` | `@Semantics.fiscal.yearPeriod: true` |
| `*FISCAL_YEAR_VARIANT*` | `@Semantics.fiscal.yearVariant: true` |

## @Semantics Contact Annotations

| Column Name Pattern | Annotation |
|---|---|
| `*EMAIL*`, `*E_MAIL*` | `@Semantics.eMail.address: true` |
| `*PHONE*`, `*TELEPHONE*` | `@Semantics.telephone.type: [{"#": "WORK"}]` |
| `*FAX*` | `@Semantics.telephone.type: [{"#": "FAX"}]` |
| `*MOBILE*`, `*CELL_PHONE*` | `@Semantics.telephone.type: [{"#": "CELL"}]` |
| `*FIRST_NAME*`, `*FIRSTNAME*` | `@Semantics.name.givenName: true` |
| `*LAST_NAME*`, `*LASTNAME*`, `*SURNAME*` | `@Semantics.name.familyName: true` |
| `*FULL_NAME*`, `*PERSON_NAME*` | `@Semantics.name.fullName: true` |

## @PersonalData Annotations (PII Detection)

All `@PersonalData` annotations follow CSN Interop spec conventions:
- **camelCase** keys: `entitySemantics`, `fieldSemantics`, `isPotentiallyPersonal`, `isPotentiallySensitive`
- **Enum notation** `{"#": "VALUE"}` for `entitySemantics` and `fieldSemantics`
- **Boolean** for `isPotentiallyPersonal` and `isPotentiallySensitive`

### Source 1: Masking Policies (from Query 5)
- Any column with an active masking policy is confirmed PII
- Add `@PersonalData.isPotentiallyPersonal: true`
- If the policy name suggests sensitivity (e.g., `MASK_*_EMAILS`, `MASK_SSN`, `MASK_PHONE`), also add `@PersonalData.isPotentiallySensitive: true`

### Source 2: Column Name Heuristics (when no masking policy exists)

| Column Name Pattern | Additional Annotation | Field Semantics (enum) |
|---|---|---|
| `*EMAIL*`, `*E_MAIL*` | `@PersonalData.isPotentiallySensitive: true` | DATA_SUBJECT_ID_TYPE |
| `*USER_ID*`, `*USERID*`, `*USER_IDS*` | `@PersonalData.fieldSemantics: {"#": "DATA_SUBJECT_ID"}` | DATA_SUBJECT_ID |
| `*PHONE*`, `*TELEPHONE*`, `*FAX*` | `@PersonalData.isPotentiallySensitive: true` | (none) |
| `*ADDRESS*`, `*STREET*`, `*POSTAL*`, `*ZIP*` | (none) | (none) |
| `*SSN*`, `*SOCIAL_SECURITY*`, `*TAX_ID*`, `*NATIONAL_ID*` | `@PersonalData.isPotentiallySensitive: true` | (none) |
| `*BIRTH*`, `*DOB*`, `*DATE_OF_BIRTH*` | `@PersonalData.isPotentiallySensitive: true` | (none) |
| `*IP_ADDRESS*`, `*IPADDRESS*` | `@PersonalData.isPotentiallySensitive: true` | (none) |
| `*FIRST_NAME*`, `*LAST_NAME*`, `*FULL_NAME*`, `*PERSON_NAME*` | (none) | (none) |
| `*HASH*`, `*HASHED*`, `*DIGEST*` | (none) | (none) |
| `*CONSENT*` | `@PersonalData.fieldSemantics: {"#": "CONSENT_ID"}` | CONSENT_ID |
| `*CONTRACT*` (+ PII context) | `@PersonalData.fieldSemantics: {"#": "CONTRACT_RELATED_ID"}` | CONTRACT_RELATED_ID |

Full list of `@PersonalData.fieldSemantics` enum values: `DATA_SUBJECT_ID`, `DATA_SUBJECT_ID_TYPE`, `CONSENT_ID`, `PURPOSE_ID`, `CONTRACT_RELATED_ID`, `DATA_CONTROLLER_ID`, `USER_ID`, `END_OF_BUSINESS_DATE`, `BLOCKING_DATE`, `END_OF_RETENTION_DATE`, `IS_BLOCKED_INDICATOR`, `DATA_CATEGORY_ID`.

- **`IS_BLOCKED_INDICATOR`**: Boolean column indicating whether a data subject is currently blocked (alternative to `BLOCKING_DATE`, which holds a date). Use for boolean flags like `IS_BLOCKED`, `BLOCKED_FLAG`, `IS_PROCESSING_BLOCKED`.
- **`DATA_CATEGORY_ID`**: Column holding a local-data-category assignment (used in privacy frameworks that classify personal data by category, e.g., regulatory categories).

### Distinguishing USER_ID vs DATA_SUBJECT_ID
System audit columns (`CREATEDBY`, `CHANGEDBY`, `MODIFIED_BY`) that track which internal user performed an action should use `@PersonalData.fieldSemantics: {"#": "USER_ID"}`. Business-facing user identifiers (`*USER_ID*`, `*USERID*`, `*CUSTOMER_ID*`) should use `DATA_SUBJECT_ID`.

### Hash/Digest Columns
Columns matching `*HASH*`, `*HASHED*`, or `*DIGEST*` are potential pseudo-identifiers. Mark with `@PersonalData.isPotentiallyPersonal: true` since hashed data can still indirectly identify individuals. Do NOT mark as `isPotentiallySensitive` unless the column name also suggests the underlying data is sensitive (e.g., `SSN_HASH`).

### Entity-Level Annotations
If ANY element in an entity has `@PersonalData` annotations, add to the entity:
```json
"@PersonalData.entitySemantics": {"#": "DATA_SUBJECT_DETAILS"}
```
Use `{"#": "DATA_SUBJECT"}` only if the entity is clearly a master data table for persons (e.g., `Users`, `Employees`, `Customers`). Use `{"#": "OTHER"}` for transactional entities that reference personal data but don't represent data subjects.

Optionally add `@PersonalData.dataSubjectRole` (string, e.g., `"Customer"`, `"Employee"`) and `@PersonalData.dataSubjectRoleDescription` (string, human-readable description).
