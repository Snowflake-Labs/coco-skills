# Validation Checklists

Pre-built checklists for validating specifications and implementations.

---

## Specification Quality Checklist

Run this checklist before approving any specification.

```markdown
## Specification Quality Checklist

### Completeness
- [ ] All user requirements captured
- [ ] Success criteria defined and measurable
- [ ] Edge cases identified
- [ ] Error scenarios documented
- [ ] Out of scope explicitly stated

### Clarity
- [ ] Requirements use EARS format
- [ ] No ambiguous terms ("fast", "user-friendly", etc.)
- [ ] Each requirement is independently testable
- [ ] No implementation details in requirements (WHAT not HOW)

### Consistency
- [ ] No conflicting requirements
- [ ] Terminology consistent throughout
- [ ] Priority clearly assigned

### Feasibility
- [ ] Technical constraints identified
- [ ] Dependencies available
- [ ] No requirements conflict with existing system

### Traceability
- [ ] Each requirement has unique ID (REQ-XXX)
- [ ] Requirements linked to user story
- [ ] Acceptance criteria linked to requirements
```

---

## Implementation Quality Checklist

Run this checklist after implementation, before marking complete.

```markdown
## Implementation Quality Checklist

### Requirements Coverage
- [ ] All REQ-XXX requirements implemented
- [ ] All acceptance criteria met
- [ ] No requirements skipped without documentation

### Code Quality
- [ ] Follows existing code patterns/style
- [ ] No hardcoded values that should be configurable
- [ ] Appropriate error handling
- [ ] No security vulnerabilities (OWASP Top 10)
  - [ ] No SQL injection
  - [ ] No XSS vulnerabilities
  - [ ] No sensitive data in logs
  - [ ] Input validation at boundaries

### Testing
- [ ] Unit tests added for new code
- [ ] Existing tests still pass
- [ ] Edge cases tested
- [ ] Error scenarios tested

### Documentation
- [ ] Code comments where logic not obvious
- [ ] API documentation updated (if applicable)
- [ ] User documentation updated (if applicable)

### Performance
- [ ] No obvious performance regressions
- [ ] Database queries optimized
- [ ] No N+1 query patterns introduced

### Minimal Changes
- [ ] Only required changes made
- [ ] No unrelated refactoring
- [ ] No "while I'm here" improvements
```

---

## Bugfix Validation Checklist

Run this checklist after bugfix implementation.

```markdown
## Bugfix Validation Checklist

### Bug Resolution
- [ ] Original reproduction steps no longer trigger bug
- [ ] All Expected Behaviors (EB-*) achieved
- [ ] Fix works in all reported environments

### Regression Prevention
- [ ] All Unchanged Behaviors (UB-*) verified
- [ ] Related functionality tested
- [ ] No new errors in logs
- [ ] Performance not degraded

### Root Cause
- [ ] Root cause correctly identified
- [ ] Fix addresses root cause (not just symptom)
- [ ] Similar patterns elsewhere checked

### Code Quality
- [ ] Changes are minimal and focused
- [ ] No unrelated modifications
- [ ] Error handling appropriate
- [ ] Test added to prevent regression
```

---

## Refactor Validation Checklist

Run this checklist after refactoring.

```markdown
## Refactor Validation Checklist

### Behavior Preservation
- [ ] All Preserved Behaviors (PB-*) verified
- [ ] Public API signatures unchanged
- [ ] External integrations working
- [ ] No functional changes introduced

### Test Results
- [ ] All existing tests pass
- [ ] Test count unchanged (or increased)
- [ ] Coverage maintained or improved

### Code Quality Improvement
- [ ] Original goal achieved
- [ ] Code more readable/maintainable
- [ ] Duplication reduced (if goal)
- [ ] Complexity reduced (if goal)

### Performance
- [ ] Performance benchmarks meet baseline
- [ ] No new memory leaks
- [ ] No increased resource usage
```

---

## Pre-Commit Checklist

Final checklist before committing changes.

```markdown
## Pre-Commit Checklist

### Code Review
- [ ] Self-reviewed all changes
- [ ] No debug code or console.log statements
- [ ] No commented-out code
- [ ] No TODO comments without tickets

### Testing
- [ ] All tests pass locally
- [ ] New functionality tested manually
- [ ] Edge cases verified

### Documentation
- [ ] Spec updated with completion status
- [ ] Code comments adequate
- [ ] README updated (if needed)

### Git Hygiene
- [ ] Commit message follows conventions
- [ ] No unrelated changes included
- [ ] No large files accidentally added
- [ ] No secrets or credentials
```

---

## Quick Validation Commands

```bash
# Run tests
npm test
# or
pytest

# Check for linting issues
npm run lint
# or
ruff check .

# Check for type errors
npm run typecheck
# or
mypy .

# Check for security issues
npm audit
# or
pip-audit

# Run all pre-commit checks
pre-commit run --all-files
```
