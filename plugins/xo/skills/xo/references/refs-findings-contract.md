---
name: refs-findings-contract
description: "Design rationale for the findings-as-contract pattern. Why the drafter is isolated from the codebase, how claim tracing works, and what error classes this prevents."
---

# Findings as Contract

The findings brief is the interface between codebase exploration and guide writing. This design prevents the most common documentation error: **unbacked claims** — statements in a guide that sound plausible but were never verified against the source.

## The Problem

When an agent writes documentation with full codebase access, it can:
1. Read code, form an understanding, and write a claim
2. Hallucinate details that "seem right" based on naming patterns
3. Mix verified observations with inferred behaviour

The reader cannot distinguish case 1 from case 2. Both look equally authoritative in the final guide.

## The Solution: Structural Isolation

The `xo-bounded-writer` (Drafter) has **no `grep`, `glob`, or `bash` tools**. It literally cannot access the codebase. Its only input is the findings brief written by `xo-bounded-writer` (Compiler) from verified test results.

This creates a hard contract:
- If a fact is in findings → drafter can state it
- If a fact is NOT in findings → drafter cannot state it (it has no way to discover it)

### Error Classes Prevented

| Error Class | Definition | How Prevented |
|-------------|-----------|---------------|
| **Unbacked claim** | Guide states something not verified | Drafter can only reference findings entries |
| **Contradiction** | Guide contradicts verified evidence | Critic compares guide claims against findings |
| **Stale claim** | Guide states outdated behaviour | Test stage verifies current behaviour, not cached knowledge |

### Error Class NOT Prevented

| Error Class | Definition | Why Not Prevented |
|-------------|-----------|-------------------|
| **Missing coverage** | Guide omits important behaviour | Requires scope/survey completeness — addressed in Survey and Step 0 (Load context) |

## Claim Tracing

Every factual claim in the guide should be traceable:

```
Guide claim → Findings entry → Test result → Source observation
```

The critic performs the first link (guide → findings). If a claim cannot be linked to a findings entry, it is flagged as UNBACKED.

## When to Bypass

For internal notes, design documents, and intent-expressing documents (which describe what SHOULD be, not what IS), write directly without the pipeline. The findings-as-contract pattern is for documentation that makes **factual claims about system behaviour**.
