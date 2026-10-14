# dvp-ewi-fixer

> Automatically resolve EWI codes in migrated source code.

## Overview

| Field | Value |
|-------|-------|
| **Category** | tracking-manager |
| **Status** | **Implemented** |
| **Output** | Modified .py files, summary of changes |
| **Depends on** | dvp-ewi-extractor (optional) |

## Responsibility

Help user fix specific EWIs -- all at once, by category/priority, or individually. Also updates the state of the fix at the EWI inventory (created by dvp-ewi-tracking-manager).

## Implementation

This skill is **already implemented**. See the full SKILL.md:

- **Skill file:** [`dvp-ewi-fixer/SKILL.md`](../../dvp-ewi-fixer/SKILL.md)
- **References:** [`dvp-ewi-fixer/references/SPRKPY*.md`](../../dvp-ewi-fixer/references/) (93 reference files covering SPRKPY1000-SPRKPY1102)

## Workflow Summary

1. **Scan** -- Glob all `.py` files and grep for `#EWI: SPRKPY\d+ =>` patterns
2. **Load Context** -- Load `references/SPRKPY<NUMBER>.md` for each unique EWI found
3. **Present Findings** -- Show summary: files, counts per type, proposed fixes
4. **Apply Fixes** -- Apply approved fixes per the loaded references, remove resolved EWI comments

## EWI Pattern

```python
#EWI: SPRKPY<NUMBER> => <INFO>
```

## Stopping Points

- If no reference file exists for an EWI code, the skill stops and reports it

## Integration with EWI Inventory

When the EWI inventory exists (from dvp-ewi-extractor), the fixer should:
- Mark resolved EWIs as `status: "resolved"` and `resolved_by: "ewi_fixer"`
- Update the resolution timestamp
- Preserve manually resolved entries

## Supported EWI Codes

Currently supports 93 EWI codes from SPRKPY1000 to SPRKPY1102. Each has a dedicated reference file with resolution guidance.
