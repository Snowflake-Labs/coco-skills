---
name: bugfix
parent_skill: spec-driven
description: "Bugfix specification workflow with regression prevention"
---

# Bugfix Spec Sub-Skill

Create bugfix specifications using the 3-part format that explicitly documents unchanged behavior to prevent regressions.

## CRITICAL RULE: One Phase at a Time

Even if the user asks for all deliverables at once, produce ONLY the current phase's output, stop for approval, then proceed to the next phase. NEVER create files or begin implementation before the current phase is approved.

## Why This Matters

Most bugs are introduced while fixing other bugs. The "Unchanged Behavior" section is the key innovation - it forces explicit documentation of what should NOT change, creating a regression safety net.

## Workflow

### PHASE 1: CLARIFY

**Objective**: Fully understand the bug and its context.

**Questions to Ask**:

1. **Reproduction**: How do I trigger this bug?
   - Steps to reproduce
   - Sample data/inputs
   - Environment specifics

2. **Expected vs Actual**: What's wrong?
   - What should happen?
   - What actually happens?
   - Error messages/logs?

3. **Impact**: How severe is this?
   - Who is affected?
   - Workarounds available?
   - Data loss or security implications?

4. **Context**: What else might be affected?
   - Related functionality
   - Recent changes that might have caused it
   - Similar past bugs

**Actions**:
1. Ask clarifying questions
2. Explore codebase to locate relevant code
3. Attempt to reproduce the issue
4. Identify root cause hypothesis

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed. Do NOT create any spec files yet.
Present the following to the user and WAIT for explicit approval:
```
Here's my analysis of the bug:

**Symptom**: {what user sees}
**Root Cause Hypothesis**: {what I think causes it}
**Location**: {file(s) involved}
**Related Code**: {what else might be affected}

Is this analysis correct? Should I create the bugfix specification?
```

Do NOT proceed until user confirms. Do NOT create `bugfix-spec.md` until this gate is passed.

---

### PHASE 2: SPECIFY (3-Part Format)

**Objective**: Create bugfix spec with explicit regression prevention.

**Actions**:
1. Create spec folder: `specs/bugfixes/{bug-id}-{description}/`
2. Generate `bugfix-spec.md` with 3-part format

**Template** (write to `specs/bugfixes/{id}-{desc}/bugfix-spec.md`):

```markdown
---
status: draft
created: {date}
bug_id: {identifier}
severity: {critical|high|medium|low}
---

# Bugfix: {Brief Description}

## Summary
{One paragraph describing the bug and its impact}

## Reproduction Steps
1. {Step 1}
2. {Step 2}
3. {Step 3}

**Environment**: {relevant environment details}
**Sample Input**: {if applicable}

---

## Part 1: Current Behavior (What's Wrong)

### CB-001: {Current behavior title}
WHEN {trigger condition}
THE SYSTEM CURRENTLY {incorrect behavior}
RESULTING IN {negative outcome}

**Evidence**:
- Error message: `{actual error}`
- Log output: `{relevant logs}`
- Screenshot/recording: {if applicable}

---

## Part 2: Expected Behavior (The Fix)

### EB-001: {Expected behavior title}
WHEN {same trigger condition}
THE SYSTEM SHALL {correct behavior}
SO THAT {positive outcome}

**Acceptance Criteria**:
- [ ] {Criterion 1}
- [ ] {Criterion 2}

### EB-002: {Additional expected behavior if needed}
{Repeat pattern...}

---

## Part 3: Unchanged Behavior (Regression Prevention)

**CRITICAL**: These behaviors MUST remain unchanged after the fix.

### UB-001: {Unchanged behavior title}
WHEN {related scenario}
THE SYSTEM SHALL CONTINUE TO {existing correct behavior}
AS IT DOES TODAY

**Verification**: {How to verify this still works}

### UB-002: {Another unchanged behavior}
WHEN {another related scenario}
THE SYSTEM SHALL CONTINUE TO {existing behavior}
AS IT DOES TODAY

**Verification**: {How to verify}

---

## Root Cause Analysis

**Location**: `{file path}:{line numbers}`

**Cause**: {Explanation of why the bug occurs}

**Fix Approach**: {How the fix will work}

---

## Testing Plan

### Verify Fix
- [ ] {Test that bug is fixed}

### Regression Tests
- [ ] UB-001: {Test unchanged behavior 1}
- [ ] UB-002: {Test unchanged behavior 2}

---

## Rollback Plan
{How to undo if fix causes problems}
```

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed. Do NOT begin implementation.
Present the following to the user and WAIT for explicit approval:
```
I've created the bugfix specification at `specs/bugfixes/{id}/bugfix-spec.md`.

**The Fix**:
- {EB-001 summary}

**Unchanged Behaviors Protected**:
- {UB-001 summary}
- {UB-002 summary}

Do you approve this specification? Pay special attention to the Unchanged Behaviors - anything else that should NOT change?
```

Do NOT proceed until user approves. After approval, update `bugfix-spec.md` frontmatter to `status: approved`, then proceed to Phase 3.

---

### PHASE 3: IMPLEMENT

**Objective**: Apply fix while preserving unchanged behaviors.

**Actions**:
1. Make minimal code changes to fix the bug
2. Verify each unchanged behavior still works
3. Document changes made

**Implementation Rules**:
1. **Minimal Change Principle**: Change ONLY what's necessary to fix the bug
2. **No Refactoring**: Do not "improve" surrounding code
3. **Verify Unchanged**: After each change, mentally verify unchanged behaviors
4. **One Fix at a Time**: If multiple bugs, create separate specs

**Progress Tracking**:
```markdown
## Implementation Log

### Changes Made
| File | Change | Unchanged Behaviors Verified |
|------|--------|------------------------------|
| `file.ts:42` | {change description} | UB-001 ✓, UB-002 ✓ |

### Commands Run
- `{test command}` - Result: {pass/fail}
```

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed to validation without presenting results.
After fix is applied, present the following and WAIT for approval:
```
Fix implemented. Summary:

**Changes**:
- `{file}:{line}`: {change description}

**Verification Status**:
- [ ] Bug fixed: {test result}
- [ ] UB-001 preserved: {verification result}
- [ ] UB-002 preserved: {verification result}

Ready for final validation?
```

---

### PHASE 4: VALIDATE

**Objective**: Comprehensive verification of fix and regression prevention.

**Checklist**:
```markdown
## Bugfix Validation Checklist

### Bug Fix Verification
- [ ] Original reproduction steps no longer trigger bug
- [ ] Expected behavior (EB-*) achieved
- [ ] Acceptance criteria met

### Regression Prevention
- [ ] UB-001: {behavior} - VERIFIED UNCHANGED
- [ ] UB-002: {behavior} - VERIFIED UNCHANGED
- [ ] No new errors in logs
- [ ] No performance degradation

### Code Quality
- [ ] Changes are minimal and focused
- [ ] No unrelated modifications
- [ ] Error handling appropriate
- [ ] No new security vulnerabilities

### Edge Cases
- [ ] {Edge case 1} - PASS
- [ ] {Edge case 2} - PASS
```

**⚠️ MANDATORY STOPPING POINT** — Do NOT commit or finalize without approval.
Present validation results and WAIT for user confirmation:
```
Bugfix validation complete:

**Bug Fixed**: {YES/NO}
**Regressions**: {NONE DETECTED / list any issues}
**Unchanged Behaviors**: {X}/{Y} verified

{Any issues or concerns}

Ready to commit this fix?
```

---

## Quick Reference

**Phase Progression**:
CLARIFY → SPECIFY (3-part) → IMPLEMENT → VALIDATE

**The 3 Parts**:
1. Current Behavior - What's wrong
2. Expected Behavior - The fix
3. Unchanged Behavior - Regression prevention

**Key Principle**: The Unchanged Behavior section is NOT optional. Every bugfix MUST document what should NOT change.
