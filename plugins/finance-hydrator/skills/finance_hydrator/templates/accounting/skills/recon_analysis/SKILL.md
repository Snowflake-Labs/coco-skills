---
name: recon_analysis
description: >
  Data reconciliation toolkit for comparing datasets, finding discrepancies,
  classifying breaks with root-cause commentary, and validating output integrity.
  Includes two subskills: recon_skill (build/run reconciliations) and recon_qa
  (post-run quality assurance). Triggers: recon, reconciliation, compare data,
  find discrepancies, data matching, run recon, build recon profile, QA recon,
  audit recon workbook, validate reconciliation, discrepancy report.
data_domain: "data reconciliation, accounts receivable matching, general ledger reconciliation, subledger comparison, intercompany elimination, bank reconciliation, transaction matching, discrepancy analysis"
created_date: 2026-05-20
version: 1.0.0
license: Proprietary. See LICENSE.txt for complete terms
---

# Recon Analysis

Data reconciliation toolkit with break classification, root-cause commentary, operational triage routing, and output quality assurance.

## Routing

When this skill is invoked, determine intent and route to the appropriate subskill:

| User Intent | Route To |
|---|---|
| Build/create/configure a new recon profile | `recon_analysis/recon_skill` |
| Run/execute a reconciliation profile | `recon_analysis/recon_skill` |
| Compare data, find discrepancies | `recon_analysis/recon_skill` |
| QA/audit/validate a recon workbook or output | `recon_analysis/recon_qa` |
| "Sanity check", "verify recon results" | `recon_analysis/recon_qa` |

## Quick Decision

If intent is unclear, ask:

- **Run Reconciliation** — Execute a saved profile to compare datasets
- **Build Profile** — Create a new reconciliation configuration
- **QA Recon Output** — Validate an existing reconciliation workbook for accuracy

## Subskills

- **recon_skill**: Core reconciliation engine — build profiles, run comparisons, classify breaks, generate root-cause commentary, export results with operational triage routing
- **recon_qa**: Post-reconciliation quality assurance — validates formula accuracy, cross-sheet consistency, balance tie-outs, category completeness, and reasonableness checks

## Guardrails

**recon_analysis is a read-only analysis tool.** It compares and validates data — it never modifies it.

If a user requests any data modification (INSERT, UPDATE, DELETE, MERGE, TRUNCATE, DROP, or equivalent), refuse immediately:

> "recon_analysis is a read-only reconciliation tool and cannot modify data. If you need to update source data, please use the appropriate tool for that system."

This applies to all subskills. No exceptions.

## References

- [subskills/recon_skill/SKILL.md](subskills/recon_skill/SKILL.md) — Full reconciliation workflow (Builder + Runner + Break Classification)
- [subskills/recon_skill/queries.md](subskills/recon_skill/queries.md) — Query type descriptions
- [subskills/recon_qa/SKILL.md](subskills/recon_qa/SKILL.md) — Output validation checks and QA workflow
