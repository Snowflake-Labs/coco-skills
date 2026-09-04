# Power BI Reverse Engineer

Convert your Power BI semantic models into Snowflake Semantic Views with Cortex Code. This skill extracts the complete model from a `.pbit`/`.pbix` file or a live model via the [Power BI Modeling MCP Server](https://github.com/microsoft/powerbi-modeling-mcp), analyzes source tables, relationships, and DAX measures, then generates a single `.sql` file containing all supporting DDL and the `CREATE OR REPLACE SEMANTIC VIEW` statement.

## How It Works

1. **Extract** the semantic model (tables, columns, relationships, DAX measures, Power Query M expressions)
2. **Analyze** Power Query sources to map logical Power BI table names to physical Snowflake tables
3. **Convert** DAX measures to SQL metrics (simple aggregations, ratios, YoY, rolling calculations)
4. **Generate** a complete `.sql` file with:
   - Supporting objects (date dimension tables, helper views for materialized FK columns)
   - The full `CREATE OR REPLACE SEMANTIC VIEW` DDL with tables, relationships, facts, dimensions, and metrics
   - Documentation of unconverted DAX measures requiring manual review

## Usage

```
$powerbi-reverse-engineer convert ~/reports/sales.pbit to a semantic view in ANALYTICS_DB.REPORTING
```

```
$powerbi-reverse-engineer reverse engineer the model open in Power BI Desktop called "Customer 360" into a semantic view
```

```
$powerbi-reverse-engineer convert semantic model "Finance Reporting" from workspace "Corp Finance" to FINANCE_DB.SEMANTIC_LAYER.FINANCE_SV
```

## Output

- `<name>_semantic_view.sql` — Single SQL file containing supporting database objects, the semantic view DDL, and documentation for any items needing manual review

## Prerequisites

- A `.pbit` or `.pbix` file, **or** the [Power BI Modeling MCP Server](https://github.com/microsoft/powerbi-modeling-mcp) running with access to your model
- Cortex Code with the skill installed
- Target Snowflake database and schema where the semantic view will be created

## License

Apache License 2.0 — see [LICENSE](LICENSE)

**Author**: Josh Crittenden
