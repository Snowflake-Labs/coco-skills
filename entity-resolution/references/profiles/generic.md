# Generic Domain Profile (Name + Address)

This profile applies when no industry-specific identifiers are available. Matching relies entirely on name and address similarity.

## Tier 1 Authoritative Identifiers

**None.** Skip Tier 1 (deterministic matching) entirely. Proceed directly to Tier 2 (fuzzy matching).

If the user identifies any domain-specific IDs during profiling, switch to the appropriate domain profile.

## Name Normalization Rules

### Terms to strip (case-insensitive)
```
LLC, INC, INCORPORATED, CORP, CORPORATION, LTD, LIMITED, PLC, LP, LLP,
CO, COMPANY, GROUP, HOLDINGS, ENTERPRISES,
DBA, D/B/A, FORMERLY, FKA, AKA,
THE, AND, OF, AT
```

### Abbreviations to expand
```
ST -> STREET
AVE -> AVENUE
BLVD -> BOULEVARD
DR -> DRIVE
RD -> ROAD
CT -> COURT
PL -> PLACE
LN -> LANE
DEPT -> DEPARTMENT
NATL -> NATIONAL
INTL -> INTERNATIONAL
```

### When to use AI_COMPLETE for name normalization
- Names with embedded location references ("ABC Company - New York Office")
- Names in mixed languages or scripts
- Names with heavy abbreviation that regex cannot reliably expand

## Address Schema

Use the standard address schema for `AI_EXTRACT`:

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

1. **`state + LEFT(zip, 3)`** — Primary for US entities with addresses.
2. **`SOUNDEX(normalized_name) + state`** — Secondary when names are the primary signal.
3. **`LEFT(UPPER(normalized_name), 4) + LEFT(zip, 3)`** — Tighter blocking for large datasets.

For datasets without addresses (name-only):
- **`SOUNDEX(normalized_name)`** — Primary.
- **`LEFT(UPPER(normalized_name), 3)`** — Secondary prefix block.

Expected block sizes vary widely. Run blocking diagnostics and adjust.

## Match Threshold Starting Points

| Decision | Cosine Similarity | Notes |
|----------|------------------|-------|
| `match` | >= 0.92 | Standard threshold |
| `probable_match` | >= 0.80 | |
| `no_match` | < 0.80 | |

**Without authoritative IDs, more pairs will land in `probable_match`.** Budget for a larger Tier 3 volume and more HITL review.

## Tier 3 AI_CLASSIFY Prompt Enhancement

Use the base prompt without domain-specific additions:

```
Record A: Name="[name_left]", Address="[address_left]"
Record B: Name="[name_right]", Address="[address_right]"

Are these the same real-world entity? Consider:
1. Are the names substantively the same (accounting for abbreviations, typos, formatting)?
2. Are the addresses the same location (accounting for formatting differences)?
3. Could these be different branches/locations of the same organization?

Categories: match, probable_match, no_match
```

## Tips for Generic Matching

- **Weight address heavily** — without authoritative IDs, address is the strongest signal after name.
- **Use JAROWINKLER_SIMILARITY** on both name and street fields as supplemental signals. Threshold >= 0.85 for name, >= 0.80 for street.
- **Consider phone/email** if available — exact match on phone or email is a strong confirmation signal even without formal IDs.
- **Expect more HITL review** — generic matching produces more ambiguous results than ID-based matching. Set expectations with the customer early.
