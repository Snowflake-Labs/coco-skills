# Output Format — Flux Analysis

## Chat Output

### Single Account Mode

```markdown
## Flux Analysis: {Account Name} ({Account Number})
**{Comparison Type}** | {Current Period} vs {Prior Period}

| Metric | Current Period | Prior Period | Variance | Variance % |
|---|---|---|---|---|
| Total | $XX,XXX,XXX.XX | $XX,XXX,XXX.XX | +/-$XX,XXX,XXX.XX | +/-X.X% |
| **Materiality** | | | ${threshold} | {pct_threshold} |
| **Material?** | | | {Yes/No} | {Yes/No} |

### Commentary

{Generated commentary using the appropriate template from commentary_patterns.md}

### Top Drivers

| Rank | {Dimension} | Current | Prior | Change | % of Total |
|---|---|---|---|---|---|
| 1 | {value} | $X | $X | +/-$X | XX% |
| 2 | {value} | $X | $X | +/-$X | XX% |
| ... | ... | ... | ... | ... | ... |
| | Others ({N} items) | $X | $X | +/-$X | XX% |
```

### Use Case Mode

```markdown
## Flux Analysis: {Use Case Name}
**{Comparison Type}** | {Current Period} vs {Prior Period}
**Accounts**: {N} total | {M} material | {K} immaterial

| GL Account | Name | Current | Prior | Variance | Var % | Material? |
|---|---|---|---|---|---|---|
| {num} | {name} | {amt} | {amt} | +/-{amt} | +/-% | Yes/No |
| ... | ... | ... | ... | ... | ... | ... |
| | **Total** | **{sum}** | **{sum}** | **+/-{sum}** | **+/-{%}** | {Yes/No} |

---

### {Account Number} — {Account Name} | {Variance} ({Var%})

{Commentary for this account — bullet drivers using • prefix}

---

### {Next Account} ...

---

### Use Case Total

{Total-level commentary summarizing the 2-3 largest cross-account drivers}
```

Use K/M formatting for amounts in the table (see Number Formatting below).
Bold material account rows.

### Batch All Mode

Same as Use Case Mode repeated per use case, with a cross-use-case summary at the end:

```markdown
## Batch Summary
| Use Case | Total Variance | Material Accounts | Flagged for Controller |
|---|---|---|---|
| {name} | +/-$X.XM | {N} | {N} |
| ... | ... | ... | ... |

**Top 5 Largest Variances (All Use Cases):**
1. {Account} ({Use Case}): +/-$X.XM — {one-line driver}
2. ...
```

---

## Excel Output

### Sheet 1: Flux Commentary

The master summary table — one row per GL account with the Driver column containing full commentary.

| Column | Content | Format |
|---|---|---|
| GL Account | Account number | Text |
| Account Name | Account name/description | Text |
| Current Period | Current period amount | Number, 2 decimals |
| Prior Period | Prior period amount | Number, 2 decimals |
| Variance | Current - Prior | Number, 2 decimals, +/- |
| Variance % | Percentage change | Percentage, 1 decimal |
| Material? | Yes / No | Text |
| Driver | Full commentary text (or "Below threshold") | Text, wrap |

For Use Case Mode: includes a Total row at bottom.
For Batch Mode: one sheet per use case, each with its own Total row.

### Sheet 2: Metadata

| Field | Value |
|---|---|
| Analysis Date | {timestamp} |
| Comparison Type | {MoM / QoQ / YTD} |
| Current Period | {period identifier} |
| Prior Period | {period identifier} |
| Materiality Threshold ($) | {configured amount} |
| Materiality Threshold (%) | {configured percentage} |
| Always-Comment Categories | {list} |
| Total Accounts Analyzed | {N} |
| Material Accounts | {N} |
| Flagged for Controller | {N} |

---

## Number Formatting Rules

| Amount Range | Format | Example |
|---|---|---|
| < $1,000 | Full number, 2 decimals | $847.23 |
| $1,000 — $499,999 | K format, 1 decimal | $85.2K, $429.4K |
| >= $500,000 | M format, 1 decimal | $0.5M, $1.1M, $8.3M |

**Rules:**
- Always prefix with +/- to indicate direction of change
- Apply to ALL amounts in chat output including subtotals
- Excel columns use full unformatted numbers (for sortability and downstream use)
- Never show $1,077.8K — use $1.1M
- If amount rounds to $0.0M, use K format instead (e.g., $53K not $0.1M)
