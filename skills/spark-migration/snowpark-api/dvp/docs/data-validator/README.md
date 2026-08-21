# Data Validator Skills

Skills focused on **automated test generation and validation** for migrated Spark pipelines in Snowpark. These skills follow the Arrange/Act/Assert (Given/When/Then) testing pattern.

## Execution Flow

The DVP pipeline is orchestrated by `dvp-orchestrator` and uses the Example 00 naming convention (`dvp/01-source`, `dvp/03-tests`, `dvp/04-results`).

```
1. dvp-orchestrator              ──► Create dvp/ workspace (01-source, 02-migrated, 02-migrated_scos, 03-tests, 04-results)
2. dvp-notebook-to-script        ──► Convert notebooks (optional)
3. dvp-entrypoint-identifier     ──► Generate 04-results/entrypoints.json
4. dvp-asg-generation            ──► Generate 04-results/XX_asg.json
5. dvp-code-adapter              ──► Adapt code in-place (session injection, env vars)
6. dvp-io-schema-identifier      ──► Generate 04-results/data_io_schema.json
7. dvp-synthetic-data-generator  ──► Generate 04-results/synthetic_data/*.csv
8. stage-conversion          ──► Convert storage paths (in-place)
9. dvp-test-setup-generator      ──► Generate 03-tests/ (pytest scaffolding + tests)
10. dvp-testing-status-manager   ──► Generate 04-results/testing_status.html
```

## Dependency Graph

```
dvp-orchestrator
  ├─► dvp-notebook-to-script (optional)
  ├─► dvp-entrypoint-identifier ──► 04-results/entrypoints.json
  ├─► dvp-asg-generation ──► 04-results/XX_asg.json
  ├─► dvp-code-adapter ──► in-place edits in 01-source/02-migrated/02-migrated_scos/
  ├─► dvp-io-schema-identifier ──► 04-results/data_io_schema.json
  ├─► dvp-synthetic-data-generator ──► 04-results/synthetic_data/*.csv
  ├─► stage-conversion (in-place code edits)
  ├─► dvp-test-setup-generator ──► 03-tests/
  └─► dvp-testing-status-manager ──► 04-results/testing_status.html
```

## Skills

| Skill | Phase | Output | Status |
|-------|-------|--------|--------|
| [dvp-orchestrator](dvp-orchestrator.md) | Coordination | DVP workspace + orchestration | **Implemented** |
| [dvp-notebook-to-script](dvp-notebook-to-script.md) | Conversion | `.ipynb.py` / `.dbx.py` + helper module | **Implemented** |
| [dvp-entrypoint-identifier](dvp-entrypoint-identifier.md) | Discovery | `04-results/entrypoints.json` | **Implemented** |
| [dvp-asg-generation](../../dvp-asg-generation/SKILL.md) | Discovery | `04-results/XX_asg.json` + anomalies | Planned |
| [dvp-code-adapter](../../dvp-code-adapter/SKILL.md) | Adaptation | In-place edits in `01-source/` / `02-migrated/` / `02-migrated_scos/` | Planned |
| [dvp-io-schema-identifier](dvp-io-schema-identifier.md) | Discovery | `04-results/data_io_schema.json` | Planned |
| [dvp-synthetic-data-generator](dvp-synthetic-data-generator.md) | Generation | `04-results/synthetic_data/*.csv` | Planned |
| [stage-conversion](stage-conversion.md) | Conversion | In-place code edits | Planned |
| [dvp-test-setup-generator](dvp-test-setup-generator.md) | Test Gen | `03-tests/` (pytest scaffolding + tests) | **Implemented** |
| [dvp-testing-status-manager](dvp-testing-status-manager.md) | Reporting | `04-results/testing_status.html` | Planned |
