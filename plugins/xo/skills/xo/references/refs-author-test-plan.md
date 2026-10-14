---
name: refs-author-test-plan
description: "Detailed protocol for handling author-provided test plans. Applicable in any review-type pathway where the subject has an author-provided test plan — not only interactive testing. Extraction patterns, merge logic, classification examples, and edge cases."
---

# Author Test Plan Protocol

When PR authors provide test plans, this protocol ensures consistent handling across all review modes.

## Extraction Patterns

### Common Test Plan Formats

**Explicit Section:**
```markdown
## Test plan
- [ ] Step 1
- [ ] Step 2
```

**Checkbox List in Body:**
```markdown
Changes:
- Added feature X

Testing:
- [ ] Verify X works
- [ ] Check Y doesn't break
```

**Inline Instructions:**
```markdown
To test: run `npm test` and verify output includes...
```

**Linked Document:**
```markdown
See [testing guide](link) for verification steps.
```

### Extraction Rules

1. Look for headings containing: "test", "verify", "check", "how to"
2. Look for checkbox lists (`- [ ]` or `- [x]`)
3. Look for numbered steps following "to test" or "verification"
4. If no explicit test plan, check PR template sections

## Code-Derived Test Generation

### Mapping Code Changes to Tests

| Code Change | Implied Test |
|-------------|--------------|
| New component/class | Instantiate and verify basic behaviour |
| New render method | Verify output in all states (loading, success, error, empty) |
| New event handler | Trigger event and verify response |
| New conditional branch | Exercise both true and false paths |
| New error handling | Trigger error condition, verify handling |
| State persistence change | Verify save and restore |
| API endpoint change | Call endpoint, verify response shape |
| CSS/styling change | Visual verification of affected elements |

### Example: UI Component PR

Code changes:
```typescript
// New card component with expand/collapse
class SqlToolCard {
  render() { ... }
  onHeaderClick() { this.expanded = !this.expanded }
  renderError(error: Error) { ... }
}
```

Code-derived tests:
1. Card renders with correct initial state
2. Click header → card expands
3. Click header again → card collapses
4. Error state renders with error styling
5. Card state persists across re-renders

## Merge Logic

### Priority Rules

| Scenario | Action |
|----------|--------|
| Author item matches derived item | Keep single entry, mark as "validated" |
| Author item not derivable from code | Keep (author knows undocumented intent) |
| Derived item not in author plan | Add with note "code-derived" |
| Author item contradicts code | Flag for clarification |

### Conflict Resolution

If author's test and code-derived test conflict:
```markdown
**Conflict detected:**
- Author says: "Verify card is always expanded"
- Code shows: `expanded` defaults to `false`

Resolution needed: Is the default behaviour correct, or is the test plan outdated?
```

Present conflict to operator before proceeding.

## Classification Examples

### AUTOMATED

- Run SQL query, check output contains expected fields
- Call API endpoint, verify response status
- Execute command, check exit code
- Read file after operation, verify content

### INTERACTIVE

- Click button, verify visual feedback
- Hover element, verify tooltip appears
- Drag item, verify drop zone highlights
- Resize panel, verify responsive behaviour
- Check accessibility (screen reader, keyboard nav)

### HYBRID

- Agent runs SQL → operator verifies card renders correctly
- Agent triggers error → operator verifies error styling
- Agent performs action → operator verifies animation/transition
- Agent saves state → operator closes/reopens app → agent checks restore

## Edge Cases

### No Test Plan Provided

If PR has no test plan:
1. Generate code-derived plan
2. Present to operator: "Author provided no test plan. Code-derived plan below. Proceed?"
3. Consider commenting on PR to request author's test plan

### Test Plan Too Vague

If test plan is non-actionable (e.g., "test it works"):
1. Expand with code-derived specifics
2. Note: "Author plan expanded with specific test cases"

### Test Plan References External System

If test plan requires access we don't have:
1. Mark those items as BLOCKED
2. Note blocker in test report
3. Proceed with testable items

### Flaky or Environment-Dependent Tests

If a test might produce inconsistent results:
1. Mark as potentially flaky
2. Run multiple times if AUTOMATED
3. Note environment requirements if INTERACTIVE
