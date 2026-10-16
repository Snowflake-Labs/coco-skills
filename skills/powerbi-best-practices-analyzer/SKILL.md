---
id: powerbi-best-practices-analyzer
name: powerbi-best-practices-analyzer
skill-name: $powerbi-bpa
description: "Analyze a Power BI semantic model against Snowflake and Power BI best practices. Produces a prioritized findings report with actionable SQL fixes."
prompt: "$powerbi-bpa analyze my Power BI file at /path/to/report.pbit"
language: en
status: Published
author: Josh Crittenden
type: community
---

# Power BI Best Practices Analyzer

# When to Use
- User provides a `.pbit` or `.pbix` file and wants to audit it against best practices
- User wants to improve Power BI + Snowflake performance
- User wants to identify DAX anti-patterns, connector issues, or modeling problems
- User has the Power BI Modeling MCP Server running and wants to audit an open model
- Do NOT use for reverse engineering into a Snowflake Semantic View (use `$powerbi-reverse-engineer` instead)

# What This Skill Provides
Performs a four-domain audit of a Power BI semantic model (Data Modeling, DAX Measures, Power Query/Connector, Performance/Security) and generates a prioritized findings report with severity ratings and Snowflake SQL/DDL fixes.

# References
- [Snowflake + Power BI Best Practices](https://medium.com/snowflake/snowflake-and-power-bi-best-practices-and-recent-improvements-183e2d970c0c)
- [Microsoft Power BI Optimization Guide](https://learn.microsoft.com/en-us/power-bi/guidance/power-bi-optimization)
- [DirectQuery Model Guidance](https://learn.microsoft.com/en-us/power-bi/guidance/directquery-model-guidance)
- [Power BI Modeling MCP Server](https://github.com/microsoft/powerbi-modeling-mcp)

# Instructions

## Step 1: Determine Input Method

**Ask** the user how they want to provide their Power BI model. Two methods are supported:

### Option A: File-based (.pbit or .pbix)

If the user provides a file path:

1. **Extract** the DataModelSchema from the archive:
   ```bash
   mkdir -p /tmp/pbi_bpa
   unzip -o "<file_path>" DataModelSchema -d /tmp/pbi_bpa
   iconv -f UTF-16LE -t UTF-8 /tmp/pbi_bpa/DataModelSchema > /tmp/pbi_bpa/model.json
   ```

2. **Parse** into structured context:
   ```python
   import json
   with open('/tmp/pbi_bpa/model.json') as f:
       data = json.load(f)
   model = data.get('model', {})
   tables = model.get('tables', [])
   relationships = model.get('relationships', [])
   roles = model.get('roles', [])
   ```

### Option B: Power BI Modeling MCP Server

If the user has the [Power BI Modeling MCP Server](https://github.com/microsoft/powerbi-modeling-mcp) running (available via `npx @microsoft/powerbi-modeling-mcp@latest --start`):

1. **Connect** to the model using MCP tools:
   - For Power BI Desktop: `Connect to '[File Name]' in Power BI Desktop`
   - For Fabric workspace: `Connect to semantic model '[Model Name]' in Fabric Workspace '[Workspace Name]'`

2. **Retrieve** the model metadata using MCP tool calls:
   - Use `model_operations` to get overall model structure
   - Use `table_operations` (list) to get all tables
   - Use `column_operations` (list) for each table to get columns with data types
   - Use `measure_operations` (list) for each table to get DAX measures
   - Use `relationship_operations` (find) to get all relationships
   - Use `security_role_operations` to get RLS definitions
   - Use `named_expression_operations` (list) to get Power Query M expressions

**Detect storage mode:** M expressions containing `Snowflake.Databases(` indicate DirectQuery/Import from Snowflake. Tables with `partitions[].source.type == "calculated"` are DAX-calculated.

**Output:** Parsed model context with tables, columns, measures, relationships, and M expressions.

## Step 2: Run Analysis Across Four Domains

**Execute** analysis across four specialist domains in sequence, each appending to a shared `findings[]` list.

**2a. Data Modeling & Relationships**
→ **Load** `data-modeling/SKILL.md`
→ Checks: star schema adherence, calculated tables/columns that should be pushed to Snowflake, relationship cardinality and cross-filtering patterns, numeric precision issues, missing column metadata
→ Returns: findings for categories A + B

**2b. DAX Measures**
→ **Load** `dax-measures/SKILL.md`
→ Checks: base measure definitions, CALCULATE overuse, IF.EAGER patterns, format string issues, measure table organization, time intelligence placement
→ Returns: findings for category C

**2c. Power Query & Connector**
→ **Load** `power-query/SKILL.md`
→ Checks: M expression transforms that should be pushed to Snowflake, custom SQL usage, ODBC vs. native Snowflake connector, relative date filtering, non-Snowflake sources
→ Returns: findings for category D

**2d. Performance & Security**
→ **Load** `performance-security/SKILL.md`
→ Checks: RLS implementation, MEDIAN on large tables (use PERCENTILE_CONT in Snowflake instead), bidirectional cross-filtering, high-cardinality slicer patterns, DirectQuery-specific anti-patterns
→ Returns: findings for category E

**Output:** Combined findings list with severity, category, affected object, description, and fix.

## Step 3: Generate Report

**Format** findings into a prioritized report:

```
## Power BI Best Practices Analysis Report
File: <filename or model name>
Date: <today>
Total Findings: N (X critical, X high, X medium, X low)

---
### CRITICAL Findings
[rule ID] [table/measure] Description | Fix

### HIGH Findings
...

### MEDIUM Findings
...

### LOW Findings
...

---
### Summary Scorecard
| Category             | Critical | High | Medium | Low |
|----------------------|----------|------|--------|-----|
| A. Data Modeling     |          |      |        |     |
| B. Relationships     |          |      |        |     |
| C. DAX Measures      |          |      |        |     |
| D. Power Query       |          |      |        |     |
| E. Performance/RLS   |          |      |        |     |
| Total                |          |      |        |     |

### Top 5 Recommended Actions
1. ...
```

Include complete Snowflake SQL/DDL for every applicable finding.

**⚠️ STOPPING POINT:** Present findings to user and wait for confirmation before saving.

## Step 4: Save & Present

**Save** the report as `<filename>_bpa_report.md` in the same directory as the input file (or the current working directory if using MCP).

**Offer** to:
1. Deep-dive any specific finding
2. Generate complete Snowflake DDL for all suggested views/tables
3. Re-run analysis on a single domain after changes
4. Apply fixes directly via MCP (if connected via MCP server)

**If error occurs:**
- File not a valid ZIP: Check file extension, try different encoding
- DataModelSchema not found: File may be corrupted or incompatible PBI version
- UTF-16 decode fails: Try UTF-16BE instead of UTF-16LE
- MCP connection fails: Verify Power BI Desktop is open or Fabric workspace is accessible
- Unknown error: Ask user for guidance

# Severity Guide

| Level | Meaning |
|-------|---------|
| CRITICAL | Incorrect results or severe performance degradation |
| HIGH | Significant perf impact or major architectural anti-pattern |
| MEDIUM | Best practice violation with moderate impact |
| LOW | Minor improvement, documentation, or maintenance issue |

# Stopping Points
- ✋ After Step 3 — Review findings before saving the report

**Resume rule:** Upon user approval, proceed directly to Step 4.

# Output
- `<filename>_bpa_report.md` — Prioritized findings report with Snowflake SQL fixes

# Examples

## Example 1: File-based analysis
User: $powerbi-bpa analyze my Power BI file at ~/reports/sales-model.pbit
Assistant: Extracts DataModelSchema, runs four-domain analysis, presents findings report with severity ratings and Snowflake DDL fixes.

## Example 2: MCP-based analysis
User: $powerbi-bpa audit the model open in Power BI Desktop called "Sales Analytics"
Assistant: Connects via Power BI Modeling MCP Server, retrieves model metadata via MCP tools, runs analysis, presents findings. Offers to apply fixes directly via MCP.

## Example 3: Fabric workspace model
User: $powerbi-bpa analyze semantic model "Finance Model" in workspace "Finance Team"
Assistant: Connects to Fabric workspace via MCP, retrieves model metadata, runs analysis, presents findings report.
