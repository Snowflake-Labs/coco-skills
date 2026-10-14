# check-agent-access

> Audit every Cortex Agent in the account and verify that a given role has all required privileges — then fix any gaps in one step.

## What it does

The skill discovers all Cortex Agents in your account, reads each agent's live specification to build a full dependency tree (semantic views, Cortex Search services, UDFs, warehouses, tables), then runs parallel `SHOW GRANTS` checks — including role-hierarchy traversal — to identify every privilege gap for a role you choose. Results come back as a per-agent gap table with ✅ / ❌ status. If gaps exist, the skill generates a ready-to-run remediation script and asks whether to execute it on the spot.

## Usage

Invoke with `$check-agent-access` or let Cortex Code activate it automatically when you say something like:

```
Audit which Cortex Agents the PUBLIC role can access and fix any gaps.
Who can use my Cortex Agents?
Check if the READER role is missing any agent grants.
```

## Example output

```
Agent: CONSUME.FUNDRAISING.FUNDRAISING_ANALYST
┌──────────────────────────────────────────────┬──────────────┬───────────┬────────┐
│ Object                                       │ Type         │ Required  │ Status │
├──────────────────────────────────────────────┼──────────────┼───────────┼────────┤
│ CONSUME.FUNDRAISING.FUNDRAISING_ANALYST      │ Agent        │ USAGE     │ ❌     │
│ CONSUME.FUNDRAISING.DONATIONS                │ Sem. View    │ SELECT    │ ❌     │
│ META.CORTEX.F_SAFE_PERPLEXITY_SEARCH         │ Function     │ USAGE     │ ✅     │
└──────────────────────────────────────────────┴──────────────┴───────────┴────────┘

Agents checked: 12  |  Dependencies checked: 40  |  Gaps found: 2

-- Remediation script
GRANT USAGE ON AGENT CONSUME.FUNDRAISING.FUNDRAISING_ANALYST TO ROLE PUBLIC;
GRANT SELECT ON SEMANTIC VIEW CONSUME.FUNDRAISING.DONATIONS TO ROLE PUBLIC;
```

## Privileges checked

| Object type | Required privilege |
|---|---|
| Agent | `USAGE` |
| Semantic view | `SELECT` |
| Cortex Search service | `USAGE` |
| Function / UDF | `USAGE` |
| Warehouse | `USAGE` |
| Table | `SELECT` |

Inherited grants via role hierarchy are resolved automatically — only direct gaps are flagged.

## Requirements

- Role with `SHOW AGENTS IN ACCOUNT` visibility (typically `SYSADMIN` or above).
- `SYSADMIN` or `ACCOUNTADMIN` context to execute remediation `GRANT` statements.

## Author

Martin Fleischmann

## License

Apache 2.0 — see [LICENSE](LICENSE).
