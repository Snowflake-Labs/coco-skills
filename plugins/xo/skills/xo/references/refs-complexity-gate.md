---
name: refs-complexity-gate
description: "TRIVIAL vs SUBSTANTIVE classification criteria for review feedback or correction-driven edit diffs. Determines whether fast-path or full production workflow applies. Works for PR comments, document review, config review, or any feedback on produced artifacts."
---

# Complexity Gate

The complexity gate is the key decision point when responding to feedback on any produced artifact or classifying a correction-driven edit diff to produced work. It determines whether a review comment or edit diff can be addressed directly (fast-path) or requires routing to **Build** (`references/act-build.md`, the proposer → critic cycle) for deeper consideration.

## Classification Criteria

### TRIVIAL (Fast-Path)

Changes that are **mechanical, localised, and low-risk**. The change is fully specified by the review comment or edit diff itself — no design judgment required.

| Example | Why Trivial |
|---------|-------------|
| Rename or swap a term (e.g., deprecated name → current) | Mechanical substitution |
| Remove a line or table row the reviewer flagged | Reviewer specified exactly what to remove |
| Fix a typo, formatting issue, or broken link | No semantic change |
| Adjust wording per reviewer's suggestion | Reviewer provided the replacement |
| Add a missing entry the reviewer explicitly specified | Reviewer provided the content |
| Apply a one-line correction the operator fully specified in the edit diff | Mechanical edit diff; no design judgment |

**Key test**: Could someone apply this review item or edit diff without understanding the broader system? If yes, it's trivial.

### SUBSTANTIVE (Full Process)

Changes that require **design judgment, affect multiple files, or introduce new behaviour**.

| Example | Why Substantive |
|---------|----------------|
| Rewrite a section or add new content | Requires understanding of what to write |
| Change control flow or logic | Risk of unintended consequences |
| Add or modify tests | Requires understanding test patterns |
| Restructure document organisation | Affects other sections |
| Comment implies broader architectural concern | Scope extends beyond the comment |
| Rework control flow across multiple call sites to apply a correction-driven edit diff | Edit diff has ripple effects and needs design judgment |

**Key test**: Does this review item or edit diff require reading code, making design decisions, or considering ripple effects? If yes, it's substantive.

This gate applies equally when classifying a correction-driven edit diff to already-produced work, not just a reviewer comment.

## Edge Cases

| Scenario | Classification | Reasoning |
|----------|---------------|-----------|
| "Add a null check here" | TRIVIAL | Fully specified, single location |
| "This needs better error handling" | SUBSTANTIVE | Vague, requires design choices |
| "Update this import" | TRIVIAL | Mechanical |
| "This approach won't scale" | SUBSTANTIVE | Architectural concern |
| "Move this to a helper function" | SUBSTANTIVE | Requires deciding what to extract and where |
| "s/foo/bar" | TRIVIAL | Explicit substitution |

## Process for Mixed Reviews

When a single review contains both trivial and substantive comments:

1. **Separate** — list trivial items and substantive items explicitly
2. **Fast-path trivial** — propose trivial fixes to operator, apply if approved
3. **Commit trivial** — separate commit for trivial fixes
4. **Assess substantive** — route substantive items to Build (`references/act-build.md`, proposer → critic cycle) before applying

If addressing any comment requires *substantive creation or design judgment*, route that work through **Build** (`references/act-build.md`) regardless of classification.

This prevents substantive items from blocking trivial fixes that could be pushed immediately.

## Operator Approval

Even trivial changes require operator approval before applying. The agent proposes the specific changes, explaining:
- What the reviewer asked for
- What the proposed fix is
- Why it's classified as trivial

The operator can reclassify any item as substantive if they disagree with the classification.
