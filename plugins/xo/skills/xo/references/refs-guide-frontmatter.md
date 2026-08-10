---
name: refs-guide-frontmatter
description: "Frontmatter template for externally-contributed guides produced by the Document stage. Applied at Promote to embed provenance metadata in the output document."
---

# Guide Frontmatter

Every guide produced by the Document stage and contributed to an external repository must carry YAML frontmatter that identifies how, when, and from what sources it was produced. This serves two purposes:

1. **Freshness assessment** — a reader can check if the code has diverged since verification
2. **Process discovery** — naming XO docs explicitly helps others find and reuse the system

If the output format does not support frontmatter or metadata, note this to the user as a non-blocking consideration.

## Template (Code-Verified Mode)

```yaml
---
title: "{title}"
generated_by: Document stage (code-verified)
generated_at: {YYYY-MM-DD}
verified_against: {commit SHA}
source_paths:
  - {path/to/examined/file1}
  - {path/to/examined/file2}
---
```

## Template (Source-Cited Mode)

```yaml
---
title: "{title}"
generated_by: Document stage (source-cited)
generated_at: {YYYY-MM-DD}
source_refs:
  - {source description 1}
  - {source description 2}
---
```

## Field Definitions

| Field | Required | Description |
|-------|----------|-------------|
| `title` | Yes | Human-readable document title |
| `generated_by` | Yes | Must be `Document stage (code-verified)` or `Document stage (source-cited)` |
| `generated_at` | Yes | Date the guide was produced |
| `verified_against` | Code-verified only | Git commit SHA the guide was verified against |
| `source_paths` | Code-verified only | List of source files examined during verification |
| `source_refs` | Source-cited only | List of external sources consulted |

## Staleness Check

For code-verified guides, a reader can assess freshness by running:

```
git diff {verified_against}..HEAD -- {source_paths}
```

If the diff is non-empty, the code has moved on and the guide may need revalidation.

## What NOT to Include

- **Test or finding counts** — without the actual evidence, these are appeal to authority
- **Pipeline internals** — subagent names, stage numbers, working directories
- **Scores or confidence ratings** — subjective and unverifiable by the reader

The frontmatter should contain only what a reader needs to assess provenance and freshness.
