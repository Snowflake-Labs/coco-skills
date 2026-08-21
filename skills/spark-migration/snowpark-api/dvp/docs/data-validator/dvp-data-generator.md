# dvp-synthetic-data-generator

> Generate synthetic test data for all pipeline inputs.

## Overview

| Field | Value |
|-------|-------|
| **Category** | data-validator |
| **Status** | Planned |
| **Output** | Synthetic test data (CSV) |
| **Depends on** | dvp-io-schema-identifier |

## Responsibility

Generate one CSV file per input entry in `data_io_schema.json` — regardless of whether the input is a file or a table. The test infrastructure handles loading CSVs into the appropriate target (local files, Hive tables, or Snowflake tables via stage).

## Inputs

| Input | Required | Description |
|-------|----------|-------------|
| `data_io_schema.json` | Yes | From `dvp/04-results/data_io_schema.json` — must already have `columns` populated by `dvp-io-schema-identifier` |
| Source code | Optional | For additional hints on data patterns/constraints |

## Outputs

### Location: `dvp/04-results/synthetic_data/`

One CSV file per input entry in `data_io_schema.json`, in a flat directory:

```
04-results/
├── data_io_schema.json
└── synthetic_data/
    ├── raw_transactions.csv
    ├── returns_data.csv
    ├── exchange_rates.csv
    ├── customer_master.csv
    └── product_catalog.csv
```

### File Naming

- **Lowercase** filename derived from the entry name: `CUSTOMER_MASTER` → `customer_master.csv`
- Always `.csv` format (even for table inputs — the test setup loads them from CSV)

### CSV Conventions

- **UPPERCASE column headers** matching Snowflake's default identifier casing
- Column names come from the `columns` field in `data_io_schema.json` (uppercased)
- Values should match the declared types in `data_io_schema.json`

Example (`customer_master.csv`):
```csv
CUSTOMER_ID,CUSTOMER_NAME,COUNTRY,JOIN_DATE
101,Alice Smith,USA,2023-01-15
102,Bob Johnson,CAN,2023-03-20
103,Charlie Brown,MEX,2023-05-10
```

## How Tests Use the Generated Data

The test infrastructure loads these CSVs differently depending on the test scenario:

| Test type | File inputs | Table inputs |
|-----------|------------|--------------|
| **Source (PySpark)** | Copied to temp dir, read via `spark.read.csv()` | Loaded into Hive tables via `insertInto()` |
| **Migrated (Snowpark)** | Uploaded to Snowflake stage via `PUT` | Uploaded to stage, then `COPY INTO` table |
| **SCOS (Snowpark Connect)** | Uploaded to Snowflake stage via `PUT` | Uploaded to stage, then `COPY INTO` table |

This is why every input gets a CSV — it's the universal interchange format that feeds all three test flavors.

## Data Generation Strategies

| Strategy | Description | When to Use |
|----------|-------------|-------------|
| **Schema-based** | Generate data purely from column types | Default |
| **Code-hint** | Use code patterns to infer value ranges/formats | When source code reveals constraints |
| **Edge-case** | Include NULLs, empty strings, min/max values | Always (mixed in) |
| **Referential** | Ensure FK relationships between related tables | When multiple related inputs exist |

## Generation Rules by Type

| Type | Generation Strategy |
|------|-------------------|
| `INT` | Random integers, include 0 and boundary values |
| `STRING` | Random strings of varying length |
| `DECIMAL(p,s)` | Random decimals with correct precision/scale |
| `DATE` | Random dates in reasonable range (last 2-3 years) |
| `TIMESTAMP` | Random timestamps with timezone awareness |
| `BOOLEAN` | Mix of true/false/null |

## Referential Integrity

When multiple inputs share column names (e.g., `customer_id` appears in both `raw_transactions.csv` and `customer_master.csv`), the generated values must be consistent:

- Generate the "master" table first (e.g., `customer_master.csv` with IDs 101-105)
- Reference tables use a subset of those IDs (e.g., `raw_transactions.csv` uses customer_id values from 101-105)
- Include at least one case with a missing/null FK to test outer join handling

## Workflow

1. **Read** `data_io_schema.json` and filter for `role: "input"` entries
2. **Verify** each entry has `columns` populated (if not, warn and skip)
3. **Identify** FK relationships across inputs by matching column names
4. **Generate** master/reference tables first, then dependent tables
5. **Include** edge cases (nulls, boundaries, empty values)
6. **Write** each CSV to `dvp/04-results/synthetic_data/{name_lowercase}.csv`

## Execution Order

```
dvp-io-schema-identifier       → data_io_schema.json (IO metadata + columns)
      │
      ▼
dvp-synthetic-data-generator  ← THIS SKILL → 04-results/synthetic_data/*.csv
      │
      ▼
dvp-test-setup-generator → 03-tests/
```

## Design Considerations

- Default to 5-15 rows per input (enough to exercise logic, small enough for fast tests)
- Should generate deterministic data (seeded random) for reproducibility
- Data should be "realistic enough" to exercise the pipeline logic (joins produce results, filters match some rows)
- Should avoid generating PII or sensitive-looking data
- All CSVs use UPPERCASE headers to match Snowflake conventions (see Testing Conventions in the main README)
