---
id: agent-audit
name: agent-audit
skill-name: $agent-audit
description: Audit and proofread a Snowflake Cortex Agent's configuration against a 22-point best-practices checklist covering correctness, instruction architecture, behavioral controls, and response quality. Use when the user asks to review, audit, proofread, or improve an agent.
prompt: "$agent-audit Review my Cortex Agent for best practices"
language: en
status: Published
author: Anastasiia Stefanska
type: community
---

# Agent Audit

# When to Use
- User asks to review, audit, proofread, or improve a Cortex Agent
- User wants to check their agent's configuration (instructions, orchestration, tool descriptions, tool resources, or profile)
- Do NOT use for building a new agent from scratch

# What This Skill Provides
A comprehensive 22-point audit of a Cortex Agent's full `agent_spec`, grouped into six categories: Correctness & Hygiene, Instruction Architecture, Audience & Domain Context, Behavioral Controls, Response Quality, and Infrastructure & Robustness. Includes an optional Semantic View quality check for agents using a Cortex Analyst tool.

# Instructions

## Step 1: Extract the Full Agent Spec

**Actions:**
1. **Run** `DESCRIBE AGENT <fully_qualified_agent_name>` to retrieve the full agent_spec
2. **If truncated**, extract in chunks using `SUBSTR($7, <offset>, 1000)` from `TABLE(RESULT_SCAN(LAST_QUERY_ID()))` until the full spec is captured

**Output:** Complete agent_spec text ready for audit

**⚠️ STOPPING POINT:** Confirm the full spec is retrieved before proceeding.

## Step 2: Run the 22-Point Checklist

**Actions:**
1. **Evaluate** every section of the agent_spec against all 22 checks below
2. **Record** each finding with its check number and severity

### Correctness & Hygiene
1. **Typos & Spelling** — Scan all instruction text, profile display_name, tool names, and tool_resources for misspellings
2. **Orphan/Placeholder Text** — Look for dangling strings, leftover TODO markers, or incomplete sentences ending with "..."
3. **Valid Tool Resource References** — Verify semantic_view names, search_service names, and warehouse fields are non-empty and plausibly correct
4. **Formatting Consistency** — Check for double spaces, inconsistent newlines, mixed bullet styles, or broken markdown

### Instruction Architecture
5. **No Cross-Layer Duplication** — Response instructions own FORMATTING; orchestration owns ROUTING/LOGIC; tool descriptions own CAPABILITY SCOPE. Flag any rule that appears in more than one layer.
6. **Guardrails Positioned First** — Off-topic refusal, scope boundaries, and safety rules must appear at the TOP of orchestration instructions, not buried in the middle or end
7. **Consolidated "Do Not" Rules** — All negative constraints must live in ONE authoritative list, not scattered across layers

### Audience & Domain Context
8. **Audience Definition Present** — Orchestration must explicitly name primary users and state what they value most
9. **Domain Context Present** — Instructions must name the specific business domain, data domains, or organization
10. **Numbers-in-Context Rule** — Every metric must include denominator/sample context (e.g., "67% based on 2 wins out of 3 closed deals," not just "67%")

### Behavioral Controls
11. **Result Count Cap** — Instructions must explicitly limit how many results are displayed (e.g., "Return at most 5 results unless the user requests more")
12. **Explicit Tool Priority** — Orchestration must state tool selection order clearly (e.g., "Always use Analyst first for quantitative questions")
13. **Keyword Signal Lists** — Orchestration must list trigger words mapped to each tool (quantitative signals → Analyst; qualitative signals → Search)
14. **Sequential Multi-Tool Pattern** — For questions filtering unstructured data by structured criteria, orchestration must enforce query-then-search: first query Analyst for record identifiers, then pass those into the Search query
15. **Empty-Results Handling** — If a structured query returns no data, the agent must report that gap honestly and NOT silently fall back to search
16. **Graceful Edge Cases** — Explicit handling must exist for: no results found, ambiguous queries, and multiple possible interpretations

### Response Quality
17. **Response Format Guidance** — Response instructions must specify structure: lead with a direct answer first, then supporting details; prose over bullet points
18. **Concrete Citation Format** — Require specific citations ("In the TechCorp discovery call"), not vague ones ("according to the data")
19. **Number Formatting Rules** — Standardize number display (e.g., deal values in K suffix: $90,000 → 90K; percentages rounded to whole numbers)
20. **Data Limitation Transparency** — When synthesizing fewer than three data points, the agent must note this explicitly (e.g., "based on two closed Enterprise deals")

### Infrastructure & Robustness
21. **Pinned Orchestration Model** — `models.orchestration` must be a specific model name, not `"auto"`. Flag `"auto"` as a risk.
22. **Tuned max_results** — Values below 5 may be too restrictive; values above 10 may flood context. Recommend 5–6 for most catalog use cases.

**Output:** Full list of findings with check number and severity

**⚠️ STOPPING POINT:** Present findings to the user before suggesting any fixes.

## Step 3: Report Findings by Severity

**Actions:**
1. **Group** all findings into three severity tiers and present as a table:
   - **Critical** — Will cause incorrect behavior, silent failures, or broken tool calls (e.g., typo in tool_resource name, missing tool priority, no guardrails)
   - **High** — Will cause poor user experience or unreliable responses (e.g., no audience definition, missing citation format, no sequential multi-tool pattern)
   - **Nice-to-have** — Polish and robustness improvements (e.g., keyword signal lists, number formatting rules, result count cap)
2. **Confirm** if all 22 checks pass: output "All 22 best-practice checks passed."

**Output:** Severity-grouped findings table

## Step 4: Semantic View Quality Checks (if applicable)

**Actions:** If the agent uses a Cortex Analyst tool backed by a semantic view, also:
1. **Check** synonyms are defined on all key dimensions (customer name, sales stage, sales rep, product line)
2. **Check** custom measures are present for any KPI requiring a formula (e.g., win rate = SUM(CASE WHEN win_status = true THEN 1 ELSE 0 END) / COUNT(*))
3. **Check** sample values are enabled on the semantic view so Cortex Analyst can infer data ranges
4. **Check** AI-generated descriptions are enabled on tables and columns to reduce query planning ambiguity

**Output:** Semantic view findings appended to the main report

## Best Practices
- Always extract the FULL agent_spec before auditing — truncated specs will miss issues
- Pay special attention to tool_resources: typos there cause silent failures
- Check the profile section (display_name, comment) as part of the audit
- Non-determinism is normal — if a use case requires consistent behavior, recommend more detailed orchestration instructions with explicit step-by-step patterns

## Instruction Layer Quick-Reference

Use this table to decide where a rule belongs before auditing or writing instructions.

| Layer | Owns | Does NOT own |
|---|---|---|
| **Orchestration instructions** | Tool selection logic, keyword signals, sequential patterns, scope boundaries, guardrails, audience definition | Output formatting, citation style, number formatting |
| **Response instructions** | Output format, tone, citation style, number formatting, data limitation transparency | Routing logic, tool selection, guardrails |
| **Tool descriptions** | What data the tool contains, when it is appropriate to use | Formatting rules, routing hierarchies that span multiple tools |

## Iteration Loop (Trace-Driven Improvement)

After testing the agent, use this loop to fix issues systematically:

1. **Identify symptom** — What did the agent do wrong? (wrong tool, bad format, speculation, vague citation)
2. **Diagnose the layer** — Wrong tool selection → fix orchestration. Poor response format → fix response instructions. Scope violation → fix boundary rules.
3. **Write a specific fix** — Include a pattern, a rationale, and an example; vague instructions produce inconsistent behavior
4. **Retest via trace** — Confirm the planning span reflects the intended tool call sequence and the response span reflects the format rules
5. **Document the pattern** — Keep a log of issues and fixes; over time this becomes a reusable instruction library

# Stopping Points
- ✋ After Step 1 — confirm full spec is retrieved before running checks
- ✋ After Step 2 — present all findings to the user before suggesting fixes

**Resume rule:** Upon user approval, proceed directly to the next step without re-asking.

# Output
A severity-grouped audit report (Critical / High / Nice-to-have) covering all 22 checks, plus optional Semantic View quality findings. If all checks pass, outputs: "All 22 best-practice checks passed."

# Examples

## Example 1: Basic audit
User: `$agent-audit Review my Cortex Agent for best practices`
Assistant: Prompts for the agent name, runs `DESCRIBE AGENT <name>`, extracts the full spec, evaluates all 22 checks, and returns a grouped findings table.

## Example 2: Audit with a Cortex Analyst tool
User: `$agent-audit Audit my sales agent including the semantic view`
Assistant: Runs all 22 checks plus the four Semantic View Quality Checks, and reports all findings grouped by severity.
