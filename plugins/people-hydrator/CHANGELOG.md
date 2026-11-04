# Changelog

## 3.1.0

- `interview_debrief`: decision support only. Removed HIRE / NO HIRE / CONDITIONAL calls, role-fit verdicts, candidate ranking, calibration-weighted scores and the quality-of-hire prediction line. Cross-candidate comparison is an unranked, alphabetical side-by-side. Added human-decision safeguards: job-related evidence only (no culture-fit, personality or protected-characteristic remarks), no automated status changes, a decision-support note on every output.
- `offer_builder`: never extracts or uses candidate pay history (current / prior pay, amounts left behind); only a voluntarily disclosed competing offer is recorded. Team pay is shown as per-currency aggregates with a minimum group size of 5, never per person. Removed the Google Sheets export; output stays in chat.
- `org_health_report`: PTO, AI adoption and performance results are aggregates only. Removed the non-adopter and low-PTO individual lists; metrics over fewer than 5 employees are suppressed.
- `job_description_builder`: no longer drops pay from postings. Every JD has a `PAY AND BENEFITS` section with a placeholder for the recruiter's approved range (the skill never looks up or invents pay) and ends with the EEO statement the user provides (verbatim) or a placeholder. Shows a note to check local, state and national job-posting laws, warns before removing the pay section or exporting with unfilled placeholders, and never claims a JD is compliant.
- README: added Legal and compliance (human review, not legal advice, hiring-tool and automated-decision laws, posting disclosures, pay data, works councils, workspace retention) and Built-in output limits; corrected the notice that said safeguards apply to setup only.
- Evals, catalog triggers and templates updated to match.

## 3.0.0

- Four stages: Discover, Bind, Tune, Hydrate. Golden cases now run as the last step of Tune (the separate Evaluate stage is gone).
- Bind grades each skill's data connection against the `binding-gate` rules in its eval plan (`eval_gate.py`, `binding_gate.json`): everything connected, connected with gaps, or key data not confirmed. Each failed rule becomes a "which table holds X?" question in Tune, answered through a validated fix.
- At most 5 Tune questions per skill; the rest are shown as assumed defaults.
- The preview asks "Does this match what you report?" for each skill's headline number.
- Hydration status: key data not confirmed is hydrated as partial with a warning; a preview nobody reviewed keeps the skill partial.
- Each hydrated skill starts with a data-connection check (one metadata query per database) and asks for re-hydration if a bound table or column is gone.
- Templates are generated from `plugins/external/people/skills` and `evals` (`plugins/external/people/_tools/sync_templates.py`); the release copy uses an allowlist (`_tools/sync_staging.py`). Maintainer tools stay in `_tools/` and never ship.
- Removed unused scripts: `score_fit`, `combine_recommendations`, `smoke_test_plan`, `validate_bindings`.
