# Pharma Domain Profile

## Tier 1 Authoritative Identifiers

| Identifier | Format | Description | Priority |
|-----------|--------|-------------|----------|
| NPI | 10 digits | National Provider Identifier (prescribers, pharmacies) | 1 |
| DEA | 2 letters + 7 digits | Drug Enforcement Administration registration | 2 |
| NCPDP | 7 digits | National Council for Prescription Drug Programs (pharmacies) | 3 |
| HIN | Variable | Health Industry Number (distributors) | 4 |
| NDC | 10-11 digits (5-4-2 or 5-4-1) | National Drug Code (products, not entities — use for product matching only) | N/A |

**Cascade order:** NPI -> DEA -> NCPDP -> HIN. Match on the highest-priority ID available.

## Name Normalization Rules

### Terms to strip (case-insensitive)
```
LLC, INC, INCORPORATED, CORP, CORPORATION, LTD, LIMITED, LP, LLP,
PHARMACY, PHCY, PHARMA, PHARMACEUTICAL, PHARMACEUTICALS,
DRUG, DRUGS, DRUGSTORE, RX, APOTHECARY,
STORE, SHOP, CENTER, CENTRE, CLINIC,
DBA, D/B/A, FORMERLY, FKA, AKA
```

### Abbreviations to expand
```
PHCY -> PHARMACY
HOSP -> HOSPITAL
MED -> MEDICAL
CTR -> CENTER
SVC -> SERVICE
SVCS -> SERVICES
HLTH -> HEALTH
NATL -> NATIONAL
INTL -> INTERNATIONAL
```

### When to use AI_COMPLETE for name normalization
- Names containing store numbers (e.g., "CVS #12345" vs "CVS PHARMACY 12345") — AI can normalize the store number format
- Names with parenthetical DBA references (e.g., "ABC Corp (dba XYZ Pharmacy)")
- Names in non-English characters requiring transliteration

## Address Schema (Detailed)

Pharma entities often share buildings. Use the detailed address schema for `AI_EXTRACT`:

```json
{
  "street_number": "Street number only",
  "street_name": "Street name without number",
  "suite_unit": "Suite, unit, floor, room, or building identifier",
  "city": "City name",
  "state": "2-letter state abbreviation (US)",
  "zip": "5-digit ZIP code (US)",
  "zip4": "ZIP+4 extension if present",
  "country": "Country ISO code if present"
}
```

**Suite/unit is critical** — multiple pharmacies or prescriber offices may share one street address. Do NOT merge entities that differ only by suite/unit.

## Blocking Strategy

Recommended blocking keys (in priority order):

1. **`state + LEFT(zip, 3)`** — Primary block. Pharmacies and prescribers are location-bound.
2. **`SOUNDEX(normalized_name) + state`** — Secondary block for chains with spelling variations.
3. **`LEFT(npi, 6)`** — If NPI is present, prefix blocking catches transposed-digit errors.

Expected block sizes: 500-5,000 entities per block for state+zip3 in typical pharma datasets.

## Match Threshold Starting Points

| Decision | Cosine Similarity | Notes |
|----------|------------------|-------|
| `match` | >= 0.90 | Lower than generic — pharma names are highly formulaic ("CVS Pharmacy" appears thousands of times) |
| `probable_match` | >= 0.78 | Wider band — chain pharmacies have many near-duplicates |
| `no_match` | < 0.78 | |

**Chain pharmacy deduplication caveat:** Two CVS locations at different addresses are **different entities**. Cosine similarity on name alone will be very high. Always weight address fields heavily. Consider adding `JAROWINKLER_SIMILARITY` on street address as a tiebreaker with threshold >= 0.85.

## Tier 3 AI_CLASSIFY Prompt Enhancement

Add to the base Tier 3 prompt for pharma:

```
Additional context: These are pharmaceutical entities (pharmacies, prescribers, distributors).
Two records at the same address but different suite/unit numbers are DIFFERENT entities.
Two records with the same name but different addresses are DIFFERENT entities (chain locations).
Focus on: (1) Do the identifiers (NPI/DEA) match or are they absent? (2) Is the address
including suite/unit the same? (3) Are name differences just formatting or do they indicate
different business entities?
```

## Entity Types

Pharma datasets typically contain mixed entity types:

| Type | Distinguishing Fields | Notes |
|------|----------------------|-------|
| Pharmacy (retail) | NCPDP, store number | Chain pharmacies are separate entities per location |
| Pharmacy (mail-order) | NCPDP, no physical storefront | May share corporate address |
| Prescriber (individual) | NPI (Type 1), DEA | Person-level entity |
| Prescriber (organization) | NPI (Type 2) | Group practice, hospital |
| Distributor | HIN, DEA | Wholesale drug distributors |

When matching, **do not match across entity types** unless explicitly requested. A pharmacy and a prescriber at the same address are different entities.
