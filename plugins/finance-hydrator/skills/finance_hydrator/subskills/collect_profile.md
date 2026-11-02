# Phase 1 — Collect Profile & Intent

Understand the user's finance role and job-to-be-done before crawling data. Keep this to one
round of questions. The profile drives business-fit scoring; Snowflake data discovery still runs
later and remains the source of truth for readiness.

Use `ask_user_question` to collect:

- **Finance function / subdepartment** — one of `fpa`, `ap_ar`, `controller`, `finops`,
  `treasury`, `revenue_finance`, `cfo`, `procurement`, or `other`.
- **Persona / role** — e.g. CFO, FP&A lead, AP manager, controller, FinOps/platform owner.
- **Jobs to be done** — 1-3 concrete workflows the user wants enabled (for example,
  "monitor overdue invoices", "investigate warehouse spend", "daily CFO briefing").
- **Relevant source systems** — ERP, procurement, expense, billing, Snowflake Account Usage,
  NetSuite, SAP, Oracle, Coupa, Workday, or other named systems.
- **Requested output** — default to `skill_plugin`. Other emitters are future-facing and should
  only be selected if explicitly supported in the catalog.

Record these as `profile`:

```json
{
  "finance_subdepartment": "ap_ar",
  "persona": "AP Manager",
  "jobs_to_be_done": ["monitor overdue invoices", "prioritize invoice exceptions"],
  "source_systems": ["NetSuite", "Coupa"],
  "requested_emitters": ["skill_plugin"]
}
```

## Guardrails

- Do not ask for PII or persist personal details beyond role/workflow metadata.
- Do not mine Slack, Gmail, Drive, Calendar, or other MCP sources in this phase unless the user
  explicitly opts in. The current finance-hydrator path does not require MCP mining.
- Do not let business fit override data safety. A high-fit skill can still be BLOCKED later if
  required data or unsafe scoping columns are missing.

**Next:** load `subskills/collect_scope.md`, passing `profile`.