# Readiness Scoring Model

Readiness is a setup score, not a correctness guarantee. **Status is the primary
signal; the percentage is secondary.** Read `status` first, then use the percent
as a within-band hint.

```text
readiness =
  0.50 * required_source_coverage
+ 0.30 * required_column_coverage
+ 0.10 * confidence_calibration
+ 0.10 * optional_enrichment_coverage
```

## Status Bands

| Status | Meaning | Displayed readiness |
|---|---|---|
| `READY` | All required sources + columns resolved, good confidence and optional coverage | ~96–100 |
| `PARTIAL` | Required sources + columns resolved, but optional enrichment or confidence degraded | clamped to ≤ 74 |
| `BLOCKED` | A required source/column/access is missing | ≤ 40 |

The `PARTIAL` clamp to 74 exists so a partial skill never displays a near-READY
score (e.g. 91%), which would blur the READY/PARTIAL boundary.

## Hard Caps

| Condition | Max Score |
|---|---:|
| Missing required source | 40 |
| Source inaccessible | 30 |
| Missing unsafe scoping column | 40 |
| No business fit | `NOT_RELEVANT` |

Unsafe scoping columns are required fields that prevent safe output scoping, such as collector/user/account ownership filters.

## Recommendation Defaults

| Status | Default |
|---|---|
| `READY` | Include |
| `PARTIAL` | Ask user |
| `BLOCKED` | Exclude |
| `NOT_RELEVANT` | Exclude |