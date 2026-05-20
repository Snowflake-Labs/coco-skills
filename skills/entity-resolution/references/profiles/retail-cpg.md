# Retail / CPG Domain Profile

## Tier 1 Authoritative Identifiers

| Identifier | Format | Description | Priority |
|-----------|--------|-------------|----------|
| GTIN/UPC | 8, 12, 13, or 14 digits | Global Trade Item Number (product-level) | 1 (products) |
| GLN | 13 digits | Global Location Number (location-level) | 1 (locations) |
| Supplier ID | Vendor-specific | Internal supplier/vendor number | 2 |
| DUNS | 9 digits | Dun & Bradstreet (corporate-level) | 3 |

**Entity vs product distinction:** GTIN/UPC identifies *products*, not *entities*. For supplier/vendor matching, use GLN or DUNS. For product matching (deduplication across catalogs), use GTIN/UPC.

## Name Normalization Rules

### Terms to strip (case-insensitive)
```
LLC, INC, INCORPORATED, CORP, CORPORATION, LTD, LIMITED, CO, COMPANY,
STORES, STORE, SHOP, SHOPS, MARKET, MARKETS, SUPERMARKET, GROCERY,
FOODS, FOOD, BRANDS, BRAND, PRODUCTS, PRODUCT,
WHOLESALE, DISTRIBUTION, DISTRIBUTING, SUPPLY, SUPPLIES,
DBA, D/B/A, FORMERLY, FKA, AKA,
GROUP, HOLDINGS, ENTERPRISES, INTERNATIONAL, GLOBAL
```

### Abbreviations to expand
```
INTL -> INTERNATIONAL
NATL -> NATIONAL
DIST -> DISTRIBUTION
MFG -> MANUFACTURING
PKG -> PACKAGING
WHSE -> WAREHOUSE
```

### Store number handling
Retail entities frequently include store numbers: "WALMART #4523", "TARGET T-1892". Normalize by:
1. Extract store number to a separate field
2. Strip from entity name for matching purposes
3. Use store number as a supplemental match signal (exact match = strong confirmation)

### When to use AI_COMPLETE for name normalization
- Private label brands with variable naming ("Great Value" vs "GV" vs store brand references)
- Supplier names with plant/facility codes embedded

## Address Schema

Use the standard address schema:

```json
{
  "street": "Full street address including number and suite/unit",
  "city": "City name",
  "state": "State or province abbreviation",
  "zip": "ZIP or postal code",
  "country": "Country name or ISO code if present"
}
```

## Blocking Strategy

Recommended blocking keys:

1. **`state + LEFT(zip, 3)`** — Primary for location-based entities (stores, warehouses).
2. **`LEFT(UPPER(normalized_name), 5) + state`** — For supplier/vendor matching where names are more distinctive.
3. **`category_code + country`** — If product category or NAICS code is available.

Expected block sizes: 1,000-10,000 for state+zip3 in national retail datasets.

## Match Threshold Starting Points

| Decision | Cosine Similarity | Notes |
|----------|------------------|-------|
| `match` | >= 0.91 | |
| `probable_match` | >= 0.79 | |
| `no_match` | < 0.79 | |

**Franchise caveat:** Franchise locations (e.g., McDonald's) may have different legal entity names (the franchisee) but the same trade name. Clarify during profiling whether the goal is to match by **trade name** (brand) or **legal entity** (franchise owner).

## Tier 3 AI_CLASSIFY Prompt Enhancement

```
Additional context: These are retail/CPG entities (retailers, suppliers, distributors,
manufacturers). Different store locations of the same chain are DIFFERENT entities.
A franchise location owned by a different legal entity is a DIFFERENT entity from
corporate-owned locations unless matching by trade name only.
Focus on: (1) Do GLN/DUNS/Supplier IDs match? (2) Is this the same physical location
or a different branch? (3) Are name differences due to DBA/trade name vs legal name?
```

## Entity Types

| Type | Distinguishing Fields | Notes |
|------|----------------------|-------|
| Retailer (store) | GLN, store number | Each location is a separate entity |
| Supplier / Vendor | Supplier ID, DUNS | Corporate-level entity |
| Manufacturer | DUNS, plant code | May have multiple facilities |
| Distributor | GLN, warehouse code | Logistics entity |
| Product | GTIN/UPC | Not an entity — match separately if needed |

## Product Matching (Supplemental)

If the engagement includes product deduplication across catalogs:
- Match on GTIN/UPC as Tier 1 (deterministic)
- Embed product description + brand + category for Tier 2
- Be cautious with pack-size variants (12-pack vs 24-pack have different GTINs but similar descriptions)
