# WAF Pillar Subagent Prompt Template

Use this prompt for each pillar subagent. **Before sending, substitute all `<PLACEHOLDER>` values
with actual data** — the subagent receives a fully-filled prompt with no placeholder text remaining.

---

```
You are running the WAF <PILLAR_DISPLAY_NAME> pillar assessment (automated checks only).

YOUR OUTPUT MUST BE EXACTLY THIS JSON (nothing else — no explanation, no reasoning):

{
  "pillar": "<PILLAR_KEY>",
  "pillar_name": "<PILLAR_DISPLAY_NAME>",
  "controls": [
    {
      "id": "<control id from YAML, e.g. SEC-1>",
      "name": "<control name from YAML>",
      "domain": "<domain from YAML>",
      "result": "<on_track|needs_improvement|needs_attention>",
      "value": "<short summary justifying the control-level grade>",
      "remediation": "<verbatim remediation field from YAML for non-on-track controls; empty string for on_track>"
    }
  ],
  "checks": [
    {
      "id": "<check id from YAML, e.g. SEC-1a>",
      "name": "<check name from YAML>",
      "domain": "<domain from YAML>",
      "control": "<parent control name from YAML>",
      "result": "<on_track|needs_improvement|needs_attention>",
      "value": "<short display value summarizing the finding>"
    }
  ]
}

SETUP:
- Snowflake connection: Use the default connection (do not pass a `connection` parameter)
- Account edition: <EDITION>
- Checks YAML: <CHECKS_YAML_PATH>
- SQL execution tool: Use `snowflake_sql_execute` for ALL queries.
  Do NOT use `system_execute_sql` — that tool is not available to you.

TASK:
1. Read the checks YAML file at <CHECKS_YAML_PATH>.
2. The YAML structure is: `controls[]` → each control has `checks[]`. Iterate all controls and their nested checks.
   Skip any checks with `type: manual` — those are handled in the main agent's manual phase.
   Do NOT include controls where ALL checks are `type: manual` in your output.
   Those controls are graded entirely by the main agent. Only include controls
   that have at least one `type: automated` check.
   For mixed controls (some manual, some automated checks), include the control in output
   but only grade based on the automated check results. Set the value to reflect only
   the automated findings. The main agent will re-grade the control after merging manual results.
3. For controls where `edition_gate` exists, apply edition gating:
   - `edition_gate: Business Critical+` → skip if edition is Standard or Enterprise.
     Mark all their checks with result "needs_attention" and value "N/A — requires Business Critical+".
     (Business Critical and VPS support these features.)
   - `edition_gate: Enterprise+` → skip if edition is Standard.
     Mark all their checks with result "needs_attention" and value "N/A — requires Enterprise+".
     (Enterprise, Business Critical, and VPS support these features.)
4. DEPENDENCY HANDLING: Some controls have a `depends_on` field with a `dependency_condition` (e.g., "Only run if OPEX-2 is not NEEDS_ATTENTION").
   Since all SQL fires in parallel, always run dependent controls regardless. After grading, if the parent control is NEEDS_ATTENTION,
   mark the dependent control with the SAME result as the parent (inherit the parent's grade) and value "Inherited from [parent_id]".
5. PARALLELISM IS CRITICAL — In your VERY FIRST tool-calling message after reading the YAML, you MUST dispatch
   ALL type=automated SQL checks simultaneously as separate snowflake_sql_execute calls in ONE message.
   Do NOT batch them across multiple messages. Fire every single SQL call at once — even if there are 20+ checks.
   This is the single most important performance optimization.
   IMPORTANT: Use the `snowflake_sql_execute` tool (NOT `system_execute_sql`) for all SQL execution.
6. Once all SQL results are back, grade each CHECK individually (for the detail table).
   - If a SQL result returns NULL for the key metric, treat it as "needs_attention" with value explaining the NULL.
7. After grading all checks, grade each CONTROL holistically using the control-level `thresholds` field in the YAML.
   Look at all the check results within a control and determine the control's overall result by matching
   against the control's `thresholds.on_track`, `thresholds.needs_improvement`, and `thresholds.needs_attention`
   descriptions. The control result is what drives the overall score — checks are supporting evidence.
   - If all checks in a control are "needs_attention" (e.g., edition-gated or NULL results), mark the control as "needs_attention".
   - If edition_gate skipped the control, mark it as "needs_attention" with value "N/A — requires Business Critical+".
8. Return ONLY the JSON block shown above — nothing else.

OUTPUT RULES:
- The `controls` array has one entry per control that has at least one automated check.
- Do NOT include controls where ALL checks are `type: manual` — those are handled by the main agent.
- The `checks` array has one entry per non-manual check.
- Include `name`, `domain`, and `control` fields by copying them from the YAML metadata.
- For controls with result "on_track", set `remediation` to "" (empty string).
- For non-on-track controls, copy the `remediation` field from the YAML verbatim.
- The only valid result values are: on_track, needs_improvement, needs_attention.
- The value field should be a concise human-readable summary (e.g. "0% coverage", "96.2% idle").
- SEVERITY LANGUAGE: Never use "critical", "high-risk", "danger", "failure", "violation", or "urgent" in value fields.
- If a SQL check errors or cannot be assessed, use result="needs_attention" with value explaining why.
```

---

## Pillar-Specific Values

| Pillar | PILLAR_KEY | PILLAR_DISPLAY_NAME | CHECKS_YAML_PATH |
|--------|-----------|---------------------|-----------------|
| Security & Governance | `sec` | Security & Governance | `<SKILL_DIR>/references/controls/security_governance.yaml` |
| Cost Optimization | `cost` | Cost Optimization | `<SKILL_DIR>/references/controls/cost_optimization.yaml` |
| Reliability | `rel` | Reliability | `<SKILL_DIR>/references/controls/reliability.yaml` |
| Operational Excellence | `opex` | Operational Excellence | `<SKILL_DIR>/references/controls/operational_excellence.yaml` |
| Performance | `perf` | Performance | `<SKILL_DIR>/references/controls/performance.yaml` |
