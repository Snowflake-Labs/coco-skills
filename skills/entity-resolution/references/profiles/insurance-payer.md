# Insurance / Payer Domain Profile

## Tier 1 Authoritative Identifiers

| Identifier | Format | Description | Priority |
|-----------|--------|-------------|----------|
| NAIC Code | 5 digits | National Association of Insurance Commissioners identifier | 1 |
| CMS Payer ID | 5 alphanumeric | Centers for Medicare & Medicaid Services payer identifier | 2 |
| Plan ID (HIOS) | 14-16 alphanumeric | Health Insurance Oversight System plan identifier (ACA marketplace) | 3 |
| Tax ID / EIN | 9 digits (XX-XXXXXXX) | Employer Identification Number | 4 |
| DUNS | 9 digits | Dun & Bradstreet Universal Numbering System | 5 |
| AM Best ID | Variable | AM Best Company Number (rating agency identifier) | 6 |

**Cascade order:** NAIC -> CMS Payer ID -> Plan ID -> Tax ID -> DUNS -> AM Best. NAIC is the gold standard for US-domiciled insurance carriers.

## Name Normalization Rules

### Terms to strip (case-insensitive)
```
LLC, INC, INCORPORATED, CORP, CORPORATION, LTD, LIMITED, CO, COMPANY,
INSURANCE, INS, ASSURANCE, INDEMNITY,
HEALTH, HEALTHCARE, HEALTH CARE,
PLAN, PLANS, HMO, PPO, EPO, POS, HDHP,
MUTUAL, GROUP, BENEFITS, BENEFIT,
LIFE, CASUALTY, PROPERTY, FIRE,
OF, THE, AND, AT,
UNDERWRITERS, UNDERWRITING
```

### Abbreviations to expand
```
INS -> INSURANCE
NATL -> NATIONAL
AMER -> AMERICAN
ASSOC -> ASSOCIATION
MGMT -> MANAGEMENT
GRP -> GROUP
HLTH -> HEALTH
BC -> BLUE CROSS
BS -> BLUE SHIELD
BCBS -> BLUE CROSS BLUE SHIELD
```

### When to use AI_COMPLETE for name normalization
- Plans with parent company references embedded ("Anthem Blue Cross - California Individual & Family Plans")
- DBA/trade name vs legal entity name (e.g., "Wellpoint Inc" dba "Anthem Blue Cross")
- Regional Blue Cross Blue Shield licensees with complex naming

## Address Schema

Standard schema (same as financial services — use country field since some reinsurers are international):

```json
{
  "street": "Full street address including number and suite/unit",
  "city": "City name",
  "state": "State or province abbreviation",
  "zip": "ZIP or postal code",
  "country": "Country name or ISO 3166-1 alpha-2 code"
}
```

**Note:** State of domicile is critical — an insurance company is chartered in one state but may operate in many. Use domicile state as the primary geographic attribute, not mailing address state.

## Blocking Strategy

Recommended blocking keys:

1. **`state_of_domicile + LEFT(UPPER(normalized_name), 4)`** — Primary. Insurers are state-regulated.
2. **`line_of_business + state_of_domicile`** — When line-of-business classification is available (life, P&C, health).
3. **`LEFT(UPPER(normalized_name), 5) + country`** — For international reinsurers.

Expected block sizes: 100-1,000 per block for state+name_prefix.

## Match Threshold Starting Points

| Decision | Cosine Similarity | Notes |
|----------|------------------|-------|
| `match` | >= 0.93 | Higher — insurance entity names are relatively unique |
| `probable_match` | >= 0.82 | |
| `no_match` | < 0.82 | |

**Subsidiary/parent caveat:** Same as financial services — "Anthem, Inc." and "Anthem Blue Cross Life and Health Insurance Company" are different legal entities (parent vs subsidiary). They should be linked but not merged unless explicitly requested. Also: a plan product ("Anthem Gold 80 HMO") is NOT the same entity as the issuing company ("Anthem Blue Cross").

## Tier 3 AI_CLASSIFY Prompt Enhancement

```
Additional context: These are insurance/payer entities (health insurers, P&C carriers,
life insurers, reinsurers, managed care organizations). A parent company and its subsidiary
are DIFFERENT legal entities. A plan product name is NOT the same entity as the issuing
company. Blue Cross Blue Shield licensees in different states are DIFFERENT entities even
though they share the BCBS brand. Focus on: (1) Do NAIC codes or CMS Payer IDs match?
(2) Is this the same legal entity or parent vs subsidiary? (3) Are they in the same
state of domicile? (4) Is one a plan product name and the other the issuing company?
```

## Entity Types

| Type | Distinguishing Fields | Notes |
|------|----------------------|-------|
| Health Insurer (commercial) | NAIC, CMS Payer ID | State-regulated, may operate multi-state |
| Medicare Advantage Plan | CMS Payer ID, Plan ID (H-number) | CMS-contracted |
| Medicaid MCO | CMS Payer ID, state contract | State-contracted managed care |
| P&C Carrier | NAIC, AM Best | Property and casualty insurer |
| Life Insurer | NAIC, AM Best | Life and annuity products |
| Reinsurer | NAIC (if US), AM Best, LEI | May be international |
| TPA (Third-Party Administrator) | Tax ID | Administers plans but does not bear risk |

When matching, **do not match across entity types** unless explicitly requested. A TPA and a health insurer at the same address are different entities.

## Multi-State Operations

Insurance companies are domiciled in one state but licensed in many. In NAIC data, the company has ONE NAIC code regardless of operating states. Do not create separate entities per operating state — deduplicate by NAIC code. However, Blue Cross Blue Shield licensees ARE separate legal entities per state/region.
