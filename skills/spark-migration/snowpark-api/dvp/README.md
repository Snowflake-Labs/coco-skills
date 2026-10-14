# DVP - Data Validation Pipeline

A collection of AI skills for **automated test generation and validation** of migrated Spark pipelines to Snowpark. DVP helps verify that migrated code produces the same results as the original source by generating test projects, managing EWI (Errors, Warnings, and Informational messages), and tracking migration quality.

## Context

DVP works **after** a migration performed by the **SMA (Snowpark Migration Accelerator)**. SMA converts PySpark (Python), Scala Spark, or mixed workloads to Snowflake using the Snowpark API. DVP currently supports PySpark or Scala workloads (mixed not yet supported). SMA takes two user-provided paths:

- **`<input>`** -- Original Python Spark source code (immutable), contains a `.snowma` project file
- **`<output>`** -- The conversion folder inside the SMA output root (e.g., `Conversion-<timestamp>/`), resolved from the `.snowma` file

DVP creates its workspace at **`<output>/dvp/`** inside the conversion folder and works with copies of both the source and migrated code so the originals are never altered. See [SMA Integration and Folder Structure](docs/sma-integration.md) for the complete layout and `.snowma` path resolution.

**Sample workloads:** Large SMA fixture trees are **not** bundled under `dvp/examples/` in the shipped skill (smaller install, fewer Windows path issues). Exercise DVP against real SMA `<input>` / `<output>` paths using the layouts in SMA Integration.

## Preconditions

Before running DVP skills, ensure you have:

- **SMA migration completed** -- Both `<input>` and `<output>` paths available
- **Cortex Code CLI installed** -- Required for code analysis and EWI processing
- **An IDE or Editor** -- CoCo Desktop (optional), Cursor, VS Code, PyCharm, or any editor of your choice

See [docs/preconditions.md](docs/preconditions.md) for detailed setup instructions.

## Skill Categories

### Data Validator (`data-validator`)

Skills focused on generating and running validation tests for migrated pipelines.

| Skill | Description | Status |
|-------|-------------|--------|
| [dvp-orchestrator](dvp-orchestrator/SKILL.md) | Orchestrates the full test generation flow | **Implemented** |
| [dvp-entrypoint-identifier](dvp-entrypoint-identifier/SKILL.md) | Scans source code to identify pipeline entry points | **Implemented** |
| [dvp-asg-generation](dvp-asg-generation/SKILL.md) | Generates ASG from source code — `XX_asg.json` | Planned |
| [dvp-code-adapter](dvp-code-adapter/SKILL.md) | Adapts source and migrated workloads for testing (session injection, enableHiveSupport, defer env vars) | Planned |
| [dvp-io-schema-identifier](dvp-io-schema-identifier/SKILL.md) | Identifies input/output files and tables + infers column schemas | Planned |
| [dvp-test-setup-generator](docs/data-validator/dvp-test-setup-generator.md) | Generates test setup code (Arrange/Given) | **Implemented** |
| [dvp-test-runner](dvp-test-runner/SKILL.md) | Runs source and migrated test suites, fixes runability issues | **Implemented** |
| [dvp-migrated-test-fixer](dvp-migrated-test-fixer/SKILL.md) | Makes migrated tests PASS by fixing Snowpark API incompatibilities via git-tracked iterations | **Implemented** |
| [dvp-test-execution-generator](docs/data-validator/dvp-test-execution-generator.md) | Generates test execution code (Act/When) | Planned |
| [dvp-test-validation-generator](docs/data-validator/dvp-test-validation-generator.md) | Generates test validation code (Assert/Then) | Planned |
| [dvp-testing-status-manager](docs/data-validator/dvp-testing-status-manager.md) | HTML dashboard for pipeline test status | Planned |
| [dvp-synthetic-data-generator](dvp-synthetic-data-generator/SKILL.md) | Generates synthetic test data from data_io_schema.json schemas | Planned |
| [stage-conversion](docs/data-validator/stage-conversion.md) | Converts S3/Azure paths to Snowflake stage syntax | Planned |

### Tracking Manager (`tracking-manager`)

Skills focused on EWI tracking, resolution, and reporting.

| Skill | Description | Status |
|-------|-------------|--------|
| [dvp-ewi-tracking-manager](docs/tracking-manager/dvp-ewi-tracking-manager.md) | HTML interface for EWI tracking | Planned |
| [dvp-ewi-fixer](dvp-ewi-fixer/SKILL.md) | Automatic EWI resolution | **Implemented** |
| [dvp-ewi-extractor](docs/tracking-manager/dvp-ewi-extractor.md) | Extracts EWI issues to JSON | Planned |
| [dvp-ewi-dashboard-generator](docs/tracking-manager/dvp-ewi-dashboard-generator.md) | Orchestrates the EWI tracking dashboard | Planned |

## Documentation

| Document | Description |
|----------|-------------|
| [SMA Integration](docs/sma-integration.md) | How DVP integrates with SMA, folder structure, and path mapping |
| [ASG Strategy](docs/asg-strategy.md) | How DVP uses the ASG for deterministic + AI hybrid analysis, with real-time anomaly feedback |
| [Skills Catalog](docs/skills-catalog.md) | Complete catalog with detailed skill descriptions |
| [Data Validator Skills](docs/data-validator/README.md) | Details on all data-validator skills |
| [Tracking Manager Skills](docs/tracking-manager/README.md) | Details on all tracking-manager skills |
| [Skill Design Guidelines](docs/skill-design-guidelines.md) | Guidelines for creating new DVP skills |
| [Preconditions](docs/preconditions.md) | Setup and prerequisites |

## Project Structure

```
skills/spark-migration/snowpark-api/dvp/
├── README.md                          # This file
├── docs/
│   ├── sma-integration.md            # SMA integration & DVP folder layout
│   ├── asg-strategy.md                # ASG-centric analysis strategy & feedback loop
│   ├── skills-catalog.md              # Full skills catalog
│   ├── skill-design-guidelines.md     # How to design a DVP skill
│   ├── preconditions.md               # Setup requirements
│   ├── data-validator/                # Docs per data-validator skill
│   └── tracking-manager/             # Docs per tracking-manager skill
├── dvp-orchestrator/                  # Implemented skill
│   └── SKILL.md
├── dvp-entrypoint-identifier/        # Implemented skill
│   └── SKILL.md
├── dvp-ewi-fixer/                     # Implemented skill
│   ├── SKILL.md
│   └── references/
│       └── SPRKPY*.md                 # EWI reference docs
```

## Testing Conventions

### Column Casing

PySpark and Snowflake handle column name casing differently:

- **PySpark** preserves the casing defined in code (typically lowercase: `sale_date`, `total_revenue`)
- **Snowflake** uppercases all unquoted identifiers (`SALE_DATE`, `TOTAL_REVENUE`)

DVP handles this at the **comparison layer**, not at the data level:

- **Input CSVs** use UPPERCASE headers (Snowflake convention) since they feed both PySpark and Snowflake
- **Baseline CSVs** (`data/expected_output/`) preserve PySpark's native lowercase output -- they are a faithful snapshot of what the source produced
- **Comparison utilities** (`compare_schemas`, `compare_dataframes`) normalize to lowercase before matching, so cross-platform casing differences never cause false failures

This means the baseline files are authoritative: they reflect the actual source output without any transformation.

### data_io_schema.json

`data_io_schema.json` in `04-results/` is the central inventory of all inputs and outputs. Column definitions use lowercase names (matching PySpark conventions). DDL generation (`build_create_table()`) uses these definitions at runtime -- Snowflake auto-uppercases unquoted identifiers, so the same definition works for both platforms.

## What Makes a Good Skill?

- **Single responsibility** -- Each skill does one thing well
- **Produces artifacts** -- Generates inventories (JSON, CSV), code (.py), scripts (.sql), reports (.html), or modifies existing ones
- **Composable** -- Can be orchestrated together by `dvp-orchestrator`

See [Skill Design Guidelines](docs/skill-design-guidelines.md) for more details.
