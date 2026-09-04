# Power BI Best Practices Analyzer

Audit your Power BI semantic models against Snowflake and Power BI best practices, all from within Cortex Code. This skill performs a four-domain analysis (Data Modeling, DAX Measures, Power Query/Connector, Performance/Security) and produces a prioritized findings report with severity ratings and actionable Snowflake SQL/DDL fixes.

## How It Works

Provide a `.pbit` or `.pbix` file, or connect to a live model via the [Power BI Modeling MCP Server](https://github.com/microsoft/powerbi-modeling-mcp), and the skill will:

1. **Extract** the semantic model metadata (tables, columns, relationships, DAX measures, M expressions)
2. **Analyze** across four domains using specialized sub-rules:
   - **Data Modeling & Relationships** — star schema adherence, calculated columns that should be pushed to Snowflake, cardinality issues
   - **DAX Measures** — CALCULATE overuse, IF.EAGER patterns, base measure definitions, organization
   - **Power Query & Connector** — transforms that belong in Snowflake, ODBC vs native connector, custom SQL usage
   - **Performance & Security** — RLS implementation, bidirectional cross-filtering, high-cardinality slicers, DirectQuery anti-patterns
3. **Generate** a prioritized report with CRITICAL/HIGH/MEDIUM/LOW findings and complete Snowflake SQL fixes

## Usage

```
$powerbi-bpa analyze my Power BI file at ~/reports/sales-model.pbit
```

```
$powerbi-bpa audit the model open in Power BI Desktop called "Sales Analytics"
```

```
$powerbi-bpa analyze semantic model "Finance Model" in workspace "Finance Team"
```

## Output

- `<filename>_bpa_report.md` — Prioritized findings report with a summary scorecard and Snowflake DDL fixes for every applicable finding

## Prerequisites

- A `.pbit` or `.pbix` file, **or** the [Power BI Modeling MCP Server](https://github.com/microsoft/powerbi-modeling-mcp) running with access to your model
- Cortex Code with the skill installed

## License

Apache License 2.0 — see [LICENSE](LICENSE)

**Author**: Josh Crittenden
