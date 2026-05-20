# Normalization SQL Templates

Entity-resolution-specific normalization patterns. These compose Snowflake SQL and Cortex AI function calls. Delegate actual AI function invocation to the `cortex-ai-functions` skill.

## Section 1: Address Parsing via AI_EXTRACT

### Standard address extraction (freeform to structured)

```sql
CREATE OR REPLACE TABLE normalized_addresses AS
SELECT
    source_id,
    raw_address,
    AI_EXTRACT(
        raw_address,
        '{
            "street": "Full street address including number and suite/unit",
            "city": "City name",
            "state": "State or province abbreviation",
            "zip": "ZIP or postal code",
            "country": "Country name or ISO code if present"
        }'
    ) AS parsed,
    parsed:street::STRING AS street,
    parsed:city::STRING AS city,
    UPPER(TRIM(parsed:state::STRING)) AS state,
    LEFT(REGEXP_REPLACE(parsed:zip::STRING, '[^0-9]', ''), 5) AS zip,
    UPPER(TRIM(COALESCE(parsed:country::STRING, 'US'))) AS country
FROM source_entities
WHERE raw_address IS NOT NULL;
```

### Detailed address extraction (suite/unit matters — pharma, healthcare)

Use the detailed schema from the loaded domain profile. Replace the extraction schema above with the profile's detailed schema that separates `street_number`, `street_name`, `suite_unit`.

## Section 2: Name Normalization

### Standard name cleanup (SQL)

```sql
-- Step 1: Uppercase and trim
-- Step 2: Remove legal suffixes (from domain profile's terms-to-strip list)
-- Step 3: Remove punctuation
-- Step 4: Collapse whitespace

CREATE OR REPLACE TABLE normalized_names AS
SELECT
    source_id,
    raw_name,
    TRIM(REGEXP_REPLACE(
        REGEXP_REPLACE(
            REGEXP_REPLACE(
                REGEXP_REPLACE(
                    UPPER(TRIM(raw_name)),
                    '\\b(LLC|INC|INCORPORATED|CORP|CORPORATION|LTD|LIMITED|CO|COMPANY)\\b',
                    ''
                ),
                -- Add domain-specific terms from profile here
                '[^A-Z0-9 ]',  -- Remove punctuation
                ''
            ),
            '\\s+',  -- Collapse whitespace
            ' '
        ),
        '^\\s+|\\s+$',  -- Final trim
        ''
    )) AS normalized_name
FROM source_entities;
```

**Adapt the regex pattern** to include all terms from the loaded domain profile's "Terms to strip" list. Build the alternation dynamically based on the profile.

### AI-assisted name normalization (edge cases only)

For names flagged during profiling as requiring AI assistance:

```sql
-- Use AI_COMPLETE for complex name normalization edge cases
SELECT
    source_id,
    raw_name,
    AI_COMPLETE(
        'mistral-large2',  -- Cost-effective for simple extraction; upgrade to claude-haiku-4-5 if results are poor
        'Normalize this business name for entity matching. '
        || 'Remove legal suffixes, expand abbreviations, remove store numbers. '
        || 'Return ONLY the normalized name, nothing else. '
        || 'Name: ' || raw_name
    ) AS ai_normalized_name
FROM source_entities
WHERE -- Apply only to flagged rows (mixed languages, embedded locations, heavy abbreviation)
    needs_ai_normalization = TRUE;
```

### Model Selection for Name Normalization

| Model | Cost | Use When |
|-------|------|----------|
| `mistral-large2` | Low | Default — handles most abbreviation expansion and suffix stripping |
| `claude-haiku-4-5` | Low | Upgrade if mistral produces too many errors on complex names |
| `claude-sonnet-4` | Medium | Mixed-language names or names requiring cultural/regional knowledge |

Start with the cheapest model and upgrade only if quality is insufficient on a 50-row sample.

## Section 3: Identifier Format Standardization

### Generic identifier cleanup

```sql
-- Strip non-alphanumeric characters and pad to expected length
SELECT
    source_id,
    raw_identifier,
    LPAD(REGEXP_REPLACE(raw_identifier, '[^A-Z0-9]', ''), <expected_length>, '0') AS normalized_id
FROM source_entities;
```

### Domain-specific identifier patterns

**NPI (10 digits):**
```sql
LPAD(REGEXP_REPLACE(raw_npi, '[^0-9]', ''), 10, '0') AS normalized_npi
```

**DEA (2 letters + 7 digits):**
```sql
UPPER(REGEXP_REPLACE(raw_dea, '[^A-Z0-9]', '')) AS normalized_dea
-- Validate: REGEXP_LIKE(normalized_dea, '^[A-Z]{2}[0-9]{7}$')
```

**LEI (20 alphanumeric, ISO 17442):**
```sql
UPPER(REGEXP_REPLACE(raw_lei, '[^A-Z0-9]', '')) AS normalized_lei
-- Validate: REGEXP_LIKE(normalized_lei, '^[A-Z0-9]{20}$')
```

**GTIN/UPC (8, 12, 13, or 14 digits):**
```sql
LPAD(REGEXP_REPLACE(raw_gtin, '[^0-9]', ''), 14, '0') AS normalized_gtin
-- Always pad to 14 digits for consistent comparison
```

**DUNS (9 digits):**
```sql
LPAD(REGEXP_REPLACE(raw_duns, '[^0-9]', ''), 9, '0') AS normalized_duns
```

**NAIC Code (5 digits — insurance/payer):**
```sql
LPAD(REGEXP_REPLACE(raw_naic, '[^0-9]', ''), 5, '0') AS normalized_naic
-- Validate: REGEXP_LIKE(normalized_naic, '^[0-9]{5}$')
-- NAIC codes are assigned by the National Association of Insurance Commissioners
```

**CMS Payer ID (5 alphanumeric — insurance/payer):**
```sql
UPPER(TRIM(REGEXP_REPLACE(raw_cms_payer_id, '[^A-Z0-9]', ''))) AS normalized_cms_payer_id
-- Validate: REGEXP_LIKE(normalized_cms_payer_id, '^[A-Z0-9]{5}$')
-- CMS-assigned identifier used in X12 837/835 transactions
```

## Section 4: Materialized Normalized Output

```sql
CREATE OR REPLACE TABLE normalized_entities AS
SELECT
    s.source_id,
    s.source_table,
    -- Normalized fields
    n.normalized_name,
    a.street AS normalized_street,
    a.city AS normalized_city,
    a.state AS normalized_state,
    a.zip AS normalized_zip,
    a.country AS normalized_country,
    -- Domain-specific normalized identifiers (adapt per profile)
    -- id.normalized_npi,
    -- id.normalized_lei,
    -- Raw originals (for HITL review)
    s.raw_name,
    s.raw_address,
    -- Blocking key (populated in Phase 3)
    NULL AS blocking_key
FROM source_entities s
LEFT JOIN normalized_names n ON s.source_id = n.source_id
LEFT JOIN normalized_addresses a ON s.source_id = a.source_id;
-- LEFT JOIN normalized_identifiers id ON s.source_id = id.source_id;
```

Uncomment and adapt the identifier joins based on the loaded domain profile.
