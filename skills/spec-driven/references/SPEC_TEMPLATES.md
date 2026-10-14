# Spec Templates

Quick-start templates for different specification types.

---

## Feature Specification Template

```markdown
---
status: draft
created: YYYY-MM-DD
feature: feature-name
---

# Feature: {Feature Name}

## Overview
{2-3 sentence description of what this feature does and why it matters}

## User Story
As a {role},
I want {capability}
so that {benefit}.

## Requirements

### REQ-001: {Primary Requirement Title}

**Type**: Event-Driven

**Statement**:
WHEN {user action or system event}
THE SYSTEM SHALL {observable behavior}
SO THAT {business value}

**Acceptance Criteria**:
- [ ] {Testable criterion 1}
- [ ] {Testable criterion 2}
- [ ] {Testable criterion 3}

### REQ-002: {Secondary Requirement Title}

**Type**: {type}

**Statement**:
WHEN {condition}
THE SYSTEM SHALL {behavior}
SO THAT {value}

**Acceptance Criteria**:
- [ ] {Criterion}

## Non-Functional Requirements

### Performance
- {Performance requirement, e.g., "Response time < 200ms"}

### Security
- {Security requirement, e.g., "Input sanitized against XSS"}

## Out of Scope
- {Explicitly excluded item 1}
- {Explicitly excluded item 2}

## Dependencies
- {External dependency 1}
- {Code dependency 2}

## Open Questions
- [ ] {Unresolved question 1}
- [ ] {Unresolved question 2}
```

---

## Bugfix Specification Template (3-Part Format)

```markdown
---
status: draft
created: YYYY-MM-DD
bug_id: BUG-XXX
severity: critical|high|medium|low
---

# Bugfix: {Brief Description of Bug}

## Summary
{One paragraph: what's broken, who's affected, and the impact}

## Reproduction Steps
1. {Prerequisite state}
2. {Action that triggers bug}
3. {Observe incorrect behavior}

**Environment**: {OS, browser, version, etc.}
**Frequency**: {Always | Sometimes | Rarely}
**Sample Data**: {If applicable}

---

## Part 1: Current Behavior (What's Wrong)

### CB-001: {Descriptive title of the bug}

WHEN {trigger condition}
THE SYSTEM CURRENTLY {incorrect/broken behavior}
RESULTING IN {negative impact}

**Evidence**:
- Error message: `{actual error text}`
- Expected: {what should happen}
- Actual: {what does happen}

---

## Part 2: Expected Behavior (The Fix)

### EB-001: {Descriptive title of correct behavior}

WHEN {same trigger condition}
THE SYSTEM SHALL {correct behavior}
SO THAT {positive outcome for user}

**Acceptance Criteria**:
- [ ] {How to verify fix works}
- [ ] {Edge case handled}

---

## Part 3: Unchanged Behavior (Regression Prevention)

**CRITICAL**: Document all related behaviors that MUST NOT change.

### UB-001: {Related behavior that must be preserved}

WHEN {related scenario}
THE SYSTEM SHALL CONTINUE TO {existing correct behavior}
AS IT DOES TODAY

**Verification**: {How to test this still works}

### UB-002: {Another behavior to preserve}

WHEN {another scenario}
THE SYSTEM SHALL CONTINUE TO {existing behavior}
AS IT DOES TODAY

**Verification**: {Test method}

---

## Root Cause Analysis

**Location**: `{file path}:{line numbers}`

**Root Cause**: {Technical explanation of why bug occurs}

**Fix Approach**: {High-level description of the fix}

---

## Testing Plan

### Fix Verification
- [ ] {Test that bug no longer occurs}
- [ ] {Test edge cases}

### Regression Tests
- [ ] UB-001: {Verify unchanged behavior}
- [ ] UB-002: {Verify unchanged behavior}

---

## Rollback Plan
{Steps to revert if fix causes issues}
```

---

## Refactor Specification Template

```markdown
---
status: draft
created: YYYY-MM-DD
refactor: refactor-name
---

# Refactor: {Refactor Name}

## Goal
{What code quality issue are we addressing and why now}

## Current State
{Description of current code structure and its problems}

## Target State
{Description of desired code structure after refactoring}

## Scope

### Files In Scope
| File | Changes |
|------|---------|
| `path/to/file1.ts` | {What will change} |
| `path/to/file2.ts` | {What will change} |

### Files Out of Scope
- `path/to/exclude.ts` - {Reason for exclusion}

---

## Preserved Behaviors

**CRITICAL**: All public contracts must remain unchanged.

### PB-001: {Public API/Interface}

**Current Signature**:
```typescript
{function or interface definition}
```

**Callers**: {List of files that depend on this}

**Verification**: {How to verify contract unchanged}

### PB-002: {External Integration}

**Current Behavior**: {Description}

**Verification**: {Integration test to run}

### PB-003: {Performance Characteristic}

**Current Metric**: {Baseline measurement}

**Verification**: {Benchmark command}

---

## Refactoring Steps

### Step 1: {Description}

**Changes**:
- [ ] {Action item}
- [ ] {Action item}

**Verify After**: {What to test}

### Step 2: {Description}

**Changes**:
- [ ] {Action item}

**Verify After**: {What to test}

---

## Testing Strategy

### Before Starting
- [ ] Run full test suite - record baseline: ___ tests passing
- [ ] Record performance baseline: ___
- [ ] Identify all callers of public interfaces

### After Each Step
- [ ] Run affected unit tests
- [ ] Verify preserved behaviors

### After Completion
- [ ] Run full test suite
- [ ] Compare to baseline
- [ ] Performance benchmark

---

## Rollback Plan
{Git commands to revert changes if needed}
```

---

## Implementation Tasks Template

```markdown
---
status: in-progress
created: YYYY-MM-DD
spec: {link to parent spec}
---

# Implementation Tasks: {Spec Name}

## Prerequisites
- [ ] Spec approved by user
- [ ] Dependencies available
- [ ] Development environment ready

## Tasks

### Task 1: {Task Title}
- **Source**: REQ-001 / EB-001 / Step 1
- **File(s)**: `path/to/file.ts`
- **Changes**: {Description of what to implement}
- **Acceptance**: {How to verify complete}
- **Status**: [ ] Not Started / [x] Complete

### Task 2: {Task Title}
- **Source**: {Requirement reference}
- **File(s)**: `path/to/file.ts`
- **Changes**: {Description}
- **Acceptance**: {Verification}
- **Status**: [ ] Not Started

## Progress Log

### {YYYY-MM-DD}
- Completed: Task 1
- Notes: {Observations, decisions made}
- Blockers: {Any issues encountered}

## Verification Checkpoints
- [ ] After Task 2: Run unit tests
- [ ] After Task 4: Integration test
- [ ] Final: Full test suite
```

---

## Amendment Templates (Evolve Workflow)

Use these templates when evolving an existing completed feature via the `evolve/SKILL.md` sub-skill.

### Amended Requirements Frontmatter

```yaml
---
status: approved
created: YYYY-MM-DD
modified: YYYY-MM-DD
feature: feature-name
amendments:
  - date: YYYY-MM-DD
    description: "Brief description of what changed"
    reqs_added: [REQ-028, REQ-029]
    reqs_deprecated: [REQ-005]
    reqs_amended: [REQ-006]
---
```

### New Requirement (Appended After Existing)

```markdown
---

### Amendment: YYYY-MM-DD — {Brief Description of Why}

#### REQ-{next sequential ID}: {Requirement Title}

**Type**: {Ubiquitous | Event-Driven | State-Driven | Optional | Complex}
**Added**: YYYY-MM-DD

**Statement**:
WHEN {trigger condition}
THE SYSTEM SHALL {observable behavior}
SO THAT {business value}

**Acceptance Criteria**:
- [ ] {Testable criterion 1}
- [ ] {Testable criterion 2}
```

### Deprecated Requirement (Added to Existing Section)

Never delete a requirement. Mark it deprecated and preserve the original content.

```markdown
#### REQ-{ID}: {Original Title}

**Status**: DEPRECATED (YYYY-MM-DD)
**Reason**: {Why this requirement is being removed — e.g., "Replaced by REQ-029" or "Feature removed per user request"}

{Original content preserved below for traceability}

**Type**: {original type}

**Statement**:
{original EARS statement — unchanged}

**Acceptance Criteria**:
{original criteria — unchanged}
```

### Amended Requirement (Modified In-Place)

```markdown
#### REQ-{ID}: {Original or Updated Title}

**Status**: AMENDED (YYYY-MM-DD)
**Change**: {What changed and why — e.g., "Changed color scale from auto-range to clamped [-3, +3]"}

**Statement** (updated):
WHEN {updated trigger condition}
THE SYSTEM SHALL {updated behavior}
SO THAT {updated value}

**Acceptance Criteria**:
- [ ] {Updated criterion 1}
- [ ] {Updated criterion 2}

**Previous Statement**:
{Original EARS statement preserved for traceability}
```

### Amended Design Section Marker

When updating `design.md`, mark changed sections:

```markdown
## Module Design

### data/snowflake_queries.py

| Function | SQL Pattern | Returns | Used By |
|----------|-------------|---------|---------|
| `get_dow30_reference()` | ... | ... | All pages |
| `get_new_function()` | ... | ... | New page | ← (Amendment YYYY-MM-DD)

### pages/6_New_Page.py (Amendment YYYY-MM-DD)

- **Layout**: {description}
- **Data calls**: {functions used}
- **Computation**: {what it computes}
```

### Appended Implementation Tasks

When amending `tasks.md`, append new tasks after existing completed ones:

```markdown
---

## Amendment Tasks: YYYY-MM-DD — {Description}

- [ ] Task {N}: {New task description}
  - File: `{file path}`
  - Changes: {description}
  - Source: REQ-{ID}

- [ ] Task {N+1}: {Next task description}
  - File: `{file path}`
  - Changes: {description}
  - Source: REQ-{ID}

## Amendment Progress Log

### YYYY-MM-DD
- Completed: {tasks}
- Notes: {observations}
```
