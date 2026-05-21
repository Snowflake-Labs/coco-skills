# Financial Services Domain Profile

## Tier 1 Authoritative Identifiers

| Identifier | Format | Description | Priority |
|-----------|--------|-------------|----------|
| LEI | 20 alphanumeric (ISO 17442) | Legal Entity Identifier — global standard | 1 |
| DUNS | 9 digits | Dun & Bradstreet Universal Numbering System | 2 |
| Tax ID / EIN | 9 digits (XX-XXXXXXX) | US Employer Identification Number | 3 |
| CRD | Up to 7 digits | Central Registration Depository (broker-dealers, advisors) | 4 |
| SWIFT/BIC | 8 or 11 alphanumeric | Bank Identifier Code | 5 |
| RSSD ID | Up to 7 digits | Federal Reserve identifier for banking institutions | 6 |

**Cascade order:** LEI -> DUNS -> Tax ID -> CRD -> SWIFT -> RSSD. LEI is the gold standard for legal entity identification.

## Name Normalization Rules

### Terms to strip (case-insensitive)
```
LLC, INC, INCORPORATED, CORP, CORPORATION, LTD, LIMITED, PLC, LP, LLP, NA, N.A.,
BANK, BANKING, TRUST, TRUST CO, TRUST COMPANY,
FINANCIAL, FINANCE, CAPITAL, ADVISORS, ADVISORY, ADVISERS,
SECURITIES, INVESTMENTS, INVESTMENT, ASSET MANAGEMENT, WEALTH MANAGEMENT,
PARTNERS, GROUP, HOLDINGS, HOLDING, CO, COMPANY,
FSB, SSB, SB, FCU, CU
```

### Abbreviations to expand
```
NATL -> NATIONAL
INTL -> INTERNATIONAL
FED -> FEDERAL
AMER -> AMERICAN
ASSOC -> ASSOCIATION
MGMT -> MANAGEMENT
INS -> INSURANCE
SVCS -> SERVICES
```

### When to use AI_COMPLETE for name normalization
- Entity names with multiple DBAs or subsidiary references
- Foreign entity names requiring transliteration (common in KYC/AML)
- Names with complex legal structure suffixes in non-English jurisdictions

## Address Schema

Use the standard address schema for `AI_EXTRACT`:

```json
{
  "street": "Full street address including number and suite/unit",
  "city": "City name",
  "state": "State or province abbreviation",
  "zip": "ZIP or postal code",
  "country": "Country name or ISO 3166-1 alpha-2 code"
}
```

**Country is critical** — financial entities are global. Always extract and normalize country.

## Blocking Strategy

Recommended blocking keys:

1. **`country + LEFT(UPPER(normalized_name), 4)`** — Primary block. Financial entities are global; name prefix partitions effectively.
2. **`state + LEFT(zip, 3)`** — Secondary for US-only datasets.
3. **`entity_type + country`** — When entity type classification is available (bank, broker-dealer, fund, etc.).

Expected block sizes: 200-2,000 entities per block for country+name_prefix in global datasets.

## Match Threshold Starting Points

| Decision | Cosine Similarity | Notes |
|----------|------------------|-------|
| `match` | >= 0.93 | Higher than generic — financial entity names are more unique |
| `probable_match` | >= 0.82 | |
| `no_match` | < 0.82 | |

**Subsidiary/parent caveat:** "JPMorgan Chase Bank, N.A." and "JPMorgan Chase & Co." are **different legal entities** (subsidiary vs parent). They should be linked but not merged unless the use case explicitly requires corporate hierarchy flattening. Flag these as `probable_match` for human review.

## Tier 3 AI_CLASSIFY Prompt Enhancement

```
Additional context: These are financial services entities (banks, broker-dealers, funds,
insurance companies, advisors). Two records may represent a parent company and its subsidiary —
these are DIFFERENT legal entities unless the user explicitly wants corporate hierarchy matching.
Focus on: (1) Do LEI/DUNS/Tax IDs match? (2) Is the legal name substantively the same or
does it indicate parent vs subsidiary? (3) Are they in the same jurisdiction?
```

## Entity Types

| Type | Distinguishing Fields | Notes |
|------|----------------------|-------|
| Bank / Depository | RSSD, SWIFT, FDIC cert | Regulated entity with charter |
| Broker-Dealer | CRD | SEC/FINRA registered |
| Investment Advisor | CRD (IA series) | SEC or state registered |
| Insurance Company | NAIC code | State-regulated |
| Fund / Vehicle | LEI, fund ticker | Investment product entity |
| Corporate (non-financial) | DUNS, EIN | Counterparty to financial entities |

**KYC/AML context:** If the use case is sanctions screening or KYC, match thresholds should be **lower** (more aggressive matching) to minimize false negatives. Flag this during profiling and adjust Tier 2 thresholds: `probable_match` >= 0.75.
