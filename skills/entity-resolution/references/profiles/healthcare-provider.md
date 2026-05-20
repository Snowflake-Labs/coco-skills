# Healthcare Provider Domain Profile

## Tier 1 Authoritative Identifiers

| Identifier | Format | Description | Priority |
|-----------|--------|-------------|----------|
| NPI | 10 digits | National Provider Identifier | 1 |
| Taxonomy Code | 10 alphanumeric (X+9 chars) | Healthcare Provider Taxonomy (specialty classification) | Supplemental |
| Medicare ID / PTAN | Variable | Provider Transaction Access Number | 2 |
| State License | Variable by state | State medical/professional license | 3 |
| Tax ID / EIN | 9 digits | Employer Identification Number (for organizations) | 4 |

**NPI types:** Type 1 = individual provider, Type 2 = organizational provider. Do not match Type 1 against Type 2 unless explicitly linking individuals to their organizations.

## Name Normalization Rules

### Terms to strip (case-insensitive)
```
LLC, INC, INCORPORATED, CORP, CORPORATION, LTD, LIMITED, PC, PLLC, PA, SC,
MD, DO, DDS, DMD, DPM, DC, OD, PHD, PHARMD, NP, RN, ARNP, CRNA, CNM,
DR, DOCTOR, PROF, PROFESSOR,
MEDICAL, MED, MEDICINE, HEALTH, HEALTHCARE,
GROUP, PRACTICE, ASSOCIATES, ASSOC, CLINIC, CLINICS,
CENTER, CENTRE, HOSPITAL, HOSP, SYSTEM, SYSTEMS, NETWORK,
OF, THE, AND, AT
```

### Person name handling (Type 1 NPI)
For individual providers:
1. Parse into LAST, FIRST, MIDDLE, SUFFIX using `AI_EXTRACT`:
   ```json
   {
     "last_name": "Family/surname",
     "first_name": "Given name",
     "middle_name": "Middle name or initial",
     "suffix": "Generational suffix (Jr, Sr, III, etc.)",
     "credential": "Professional credential (MD, DO, NP, etc.)"
   }
   ```
2. Normalize to `LAST, FIRST` format for matching
3. Strip credentials — they are classification metadata, not identity

### Organization name handling (Type 2 NPI)
Apply standard business name normalization (strip legal suffixes, expand abbreviations).

### Abbreviations to expand
```
HOSP -> HOSPITAL
MED -> MEDICAL
CTR -> CENTER
UNIV -> UNIVERSITY
COMM -> COMMUNITY
REHAB -> REHABILITATION
ORTHO -> ORTHOPEDIC
PEDS -> PEDIATRIC
OB/GYN -> OBSTETRICS AND GYNECOLOGY
```

## Address Schema (Detailed)

Healthcare providers frequently share buildings. Use the detailed schema:

```json
{
  "street_number": "Street number only",
  "street_name": "Street name without number",
  "suite_unit": "Suite, unit, floor, building, or room identifier",
  "city": "City name",
  "state": "2-letter state abbreviation (US)",
  "zip": "5-digit ZIP code (US)",
  "zip4": "ZIP+4 extension if present"
}
```

**Suite/unit is critical** — a medical office building at one address may house 20+ separate provider practices.

## Blocking Strategy

Recommended blocking keys:

1. **`state + LEFT(zip, 3)`** — Primary. Providers are location-bound.
2. **`SOUNDEX(last_name) + state`** — For individual provider (Type 1) deduplication.
3. **`taxonomy_prefix + state`** — Group by specialty and state (taxonomy_prefix = first 3 chars).

Expected block sizes: 500-5,000 per block for state+zip3.

## Match Threshold Starting Points

| Decision | Cosine Similarity | Notes |
|----------|------------------|-------|
| `match` | >= 0.91 | |
| `probable_match` | >= 0.80 | |
| `no_match` | < 0.80 | |

**Group practice caveat:** A provider may appear individually (Type 1 NPI) and as part of a group (Type 2 NPI). These are **different entities** — one is a person, the other is an organization. The individual is a *member of* the group, not a duplicate.

## Tier 3 AI_CLASSIFY Prompt Enhancement

```
Additional context: These are healthcare providers (doctors, nurses, clinics, hospitals,
group practices). NPI Type 1 (individual) and Type 2 (organization) are DIFFERENT entity
types — do not match across types. Providers at the same address but different suites
are DIFFERENT entities. A provider may practice at multiple locations — match by NPI
not by address alone.
Focus on: (1) Do NPIs match? (2) Is this the same NPI type? (3) For individuals,
do last name + first name match (credentials and middle initials may vary)?
(4) For organizations, is it the same practice or a different practice at the same address?
```

## Entity Types

| Type | Distinguishing Fields | Notes |
|------|----------------------|-------|
| Individual Provider | NPI Type 1, credential | Person-level entity |
| Group Practice | NPI Type 2, Tax ID | Organization of providers |
| Hospital / Facility | NPI Type 2, facility code | Institutional provider |
| Health System | Tax ID, system affiliation | Parent organization (multiple facilities) |

## Multi-Location Providers

Individual providers may practice at 2+ locations. In NPPES data, each practice location gets a separate record but the **same NPI**. These are the same entity with multiple addresses — deduplicate by NPI, retain all addresses as attributes.
