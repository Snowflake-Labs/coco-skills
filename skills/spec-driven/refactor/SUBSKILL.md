
# Refactor Spec Sub-Skill

Create specifications for code refactoring that ensure zero behavior change while improving code quality.

## CRITICAL RULE: One Phase at a Time

Even if the user asks for all deliverables at once, produce ONLY the current phase's output, stop for approval, then proceed to the next phase. NEVER create files or begin implementation before the current phase is approved.

## Core Principle

**Refactoring changes code structure WITHOUT changing behavior.** This skill enforces explicit documentation of preserved behaviors, similar to bugfix unchanged behaviors.

## Workflow

### PHASE 1: CLARIFY

**Objective**: Understand the refactoring goals and constraints.

**Questions to Ask**:

1. **Motivation**: Why refactor?
   - Technical debt reduction
   - Performance improvement
   - Maintainability
   - Preparation for new feature

2. **Scope**: What's being refactored?
   - Specific files/modules
   - Architectural patterns
   - Code organization

3. **Constraints**: What must NOT change?
   - Public APIs
   - External integrations
   - Performance characteristics
   - User-facing behavior

4. **Success Criteria**: How do we verify success?
   - Code metrics improvement
   - Test coverage maintained
   - Performance benchmarks

**Actions**:
1. Explore codebase to understand current structure
2. Identify all callers/consumers of code being refactored
3. Document existing test coverage

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed. Do NOT create any spec files yet.
Present the following to the user and WAIT for explicit approval:
```
Here's my understanding of the refactoring:

**Goal**: {what we're improving}
**Scope**: {files/modules involved}
**Preserved Behaviors**: {what must NOT change}
**Risk Areas**: {potential issues to watch}

Is this scope correct? Should I create the specification?
```

---

### PHASE 2: SPECIFY

**Objective**: Create refactor spec with explicit behavior preservation.

**Template** (write to `specs/refactors/{name}/spec.md`):

```markdown
---
status: draft
created: {date}
refactor: {refactor-name}
---

# Refactor: {Refactor Name}

## Goal
{Clear statement of what we're improving and why}

## Scope

### Files In Scope
| File | Refactoring Changes |
|------|---------------------|
| `path/to/file.ts` | {What will change} |

### Files Out of Scope
- `path/excluded.ts` - {reason}

## Preserved Behaviors

**CRITICAL**: These behaviors/interfaces MUST remain unchanged.

### PB-001: {Preserved behavior title}
**Current Behavior**: {description}
**Verification**: {how to test it still works}

### PB-002: {Preserved API contract}
**Current API**: 
```typescript
{current function signature}
```
**Callers**: {list of files/functions that call this}
**Verification**: {all callers still work}

### PB-003: {Preserved performance characteristic}
**Current Performance**: {metric}
**Verification**: {benchmark to run}

## Refactoring Steps

### Step 1: {Description}
- [ ] {Action item}
- [ ] Verify PB-001
- [ ] Verify PB-002

### Step 2: {Description}
- [ ] {Action item}
- [ ] Verify preserved behaviors

## Testing Strategy

### Before Refactoring
- [ ] Run full test suite - capture baseline
- [ ] Document current behavior of key scenarios
- [ ] Benchmark performance (if relevant)

### After Each Step
- [ ] Run affected tests
- [ ] Verify preserved behaviors

### After Completion
- [ ] Run full test suite
- [ ] Compare to baseline
- [ ] Run performance benchmarks

## Rollback Plan
{How to undo if issues discovered}
```

**⚠️ MANDATORY STOPPING POINT** — Do NOT proceed. Do NOT begin implementation.
Present specification to the user and WAIT for explicit approval.

Do NOT proceed until user approves. After approval, update `spec.md` frontmatter to `status: approved`, then proceed to Phase 3.

---

### PHASE 3: IMPLEMENT

**Objective**: Execute refactoring step by step with continuous verification.

**Implementation Rules**:
1. **Small Steps**: Make one logical change at a time
2. **Continuous Verification**: Test after each step
3. **No Feature Work**: Do NOT add functionality during refactoring
4. **Preserve Tests**: Keep existing tests passing

**Progress Tracking**:
```markdown
## Refactoring Progress

### Step 1: {Description}
- Status: COMPLETE
- Changes: {summary}
- Tests: PASS
- Preserved Behaviors: ALL VERIFIED

### Step 2: {Description}
- Status: IN PROGRESS
```

**⚠️ MANDATORY STOPPING POINT** — After each major step, present progress and verify with user before continuing.

---

### PHASE 4: VALIDATE

**Template** (write to `specs/refactors/{name}/validation.md`):

```markdown
---
status: complete
validated: {date}
---

# Refactor Validation: {Refactor Name}

## Test Results

### Before Refactoring
- Test suite: {X} tests, {Y} passing
- Coverage: {Z}%
- Performance: {metric}

### After Refactoring
- Test suite: {X} tests, {Y} passing
- Coverage: {Z}%
- Performance: {metric}

## Preserved Behavior Verification

- [ ] PB-001: {behavior} - VERIFIED
- [ ] PB-002: {API contract} - VERIFIED
- [ ] PB-003: {performance} - VERIFIED

## Code Quality Metrics

| Metric | Before | After |
|--------|--------|-------|
| Lines of code | {X} | {Y} |
| Cyclomatic complexity | {X} | {Y} |
| Duplicated code | {X}% | {Y}% |

## Conclusion

{Summary of refactoring success}
```

**⚠️ MANDATORY STOPPING POINT** — Do NOT commit or finalize without approval.
Present validation results and WAIT for user confirmation:
```
Refactoring validation complete:

**Behavior Preserved**: {YES/NO}
**Tests**: {X} passing (same as before)
**Code Quality**: {improved metrics}

Ready to commit this refactoring?
```

---

## Quick Reference

**Phase Progression**:
CLARIFY → SPECIFY → IMPLEMENT (iterative) → VALIDATE

**Key Principle**: Every public interface and external behavior MUST be documented as "Preserved Behavior" and verified after each step.

**Common Preserved Behaviors**:
- Public function signatures
- API endpoints and contracts
- Configuration options
- Event emissions
- Error messages and codes
- Performance characteristics
