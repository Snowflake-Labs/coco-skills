---
name: cortex-chart-customization
title: Cortex Chart Customization
summary: Generate ready-to-paste <chart_customization> blocks for Cortex Agents and Semantic Models.
description: >-
  Use when a user wants to customize chart appearance for Cortex Agents or Semantic Models:
  enforce brand colors, set fonts, fix axis ranges, control sort order, apply number
  formatting, or enforce specific chart types. Triggers: "set up chart customization",
  "brand colors for charts", "always use bar chart", "format y-axis as dollars",
  "set chart font", "chart theme", "dark background charts", "enforce chart style",
  "viz policy", "vega template for charts".
tools:
  - Read
  - Write
  - Edit
prompt: "$cortex-chart-customization set up brand colors and a dark theme for my Cortex Agent's charts"
language: en
status: Published
author: Snowflake
type: snowflake
demo-url: ""
---

# Generate Cortex Chart Customization

This skill generates a ready-to-paste `<chart_customization>` block based on the
user's requirements. The block is placed inside either:

- **Agent** `instructions.orchestration` — applies to every chart across all semantic models
- **Semantic Model** `custom_instructions` (or `module_custom_instructions.sql_generation`) — overrides agent settings for that SM only

### Application order (when both levels are active)

```
LLM generates chart spec
        │
        ▼
① Agent vega_template merged  (baseline)
        │
        ▼
② SM vega_template merged      (SM wins on overlap)
        │
        ▼
③ CompleteScales               (auto-fill palette, prune phantom legends)
        │
        ▼
④ Viz policies evaluated       (agent + SM merged; SM wins by policy name)
```

Soft instructions follow the same order: agent text is prepended, SM text is appended.

> Full reference: [references/CHART_QUICKREF.md](references/CHART_QUICKREF.md)

---

## Step 1: Gather requirements

Ask the user which of these they want (they may want multiple):

| # | What | Questions to ask |
|---|------|-----------------|
| A | **Theme / background** | Dark or light? Background color? |
| B | **Fonts** | Font family? (e.g. `monospace`, `serif`, or leave default) |
| C | **Brand / per-value colors** | Which column? Which values → which hex colors? |
| D | **Per-value shapes** | Which column? Which values → which shapes (`diamond`, `square`, `triangle-up`, `circle`, `cross`)? |
| E | **Number formatting** | Which column and channel? Dollar (`$,.0f`), percentage (`.1%`), SI-prefix (`.2s`), custom D3 format? |
| F | **Axis range / zero baseline** | Which channel (`x` or `y`)? Fixed min? Fixed max? |
| G | **Sort order** | Which column and channel? Ascending / descending / data order (`none`) / custom list? |
| H | **Force chart type** | Which column triggers the rule? Which mark type to force (`bar`, `line`, `point`, …)? |
| I | **Free-form LLM instruction** | Any other chart style requirement to send to the LLM? |

Also ask:
- **Level**: Agent-wide or specific to one semantic model?
- **Merge mode for `vega_template`**: `override` (template wins, default) or `extend` (keep LLM spec, add missing values)?

---

## Step 2: Build the block

Use the templates below. Combine only the sections the user actually needs.

### Template skeleton

```xml
<chart_customization>
[SOFT_INSTRUCTIONS if any]
[vega_template: block if theme/font/static colors needed]
[viz_policies: block if rule-based enforcement needed]
</chart_customization>
```

**Order is mandatory**: `vega_template:` MUST come before `viz_policies:`.
The parser strips everything from `viz_policies:` onward before parsing `vega_template:`, so reversing them silently discards the template.

**Bare JSON shortcut**: If the entire block body is valid JSON (no `vega_template:` marker), it is treated as a bare template and merged deterministically — equivalent to writing `vega_template:` before the JSON.

**Merge rules** (both modes):
- The `data` block is never overwritten.
- Encoding overrides apply only when the template's `field` matches the chart's `field`, or the template omits `field`.
- After merging, `domain` entries not present in the actual data are automatically removed.

---

### A+B · Theme and fonts (`vega_template`)

```json
{
  "background": "#1a1a2e",
  "config": {
    "title": {"font": "monospace", "fontSize": 16, "fontWeight": "bold", "color": "#ffffff"},
    "axis": {
      "labelColor": "#cccccc", "titleColor": "#ffffff",
      "labelFont": "monospace", "titleFont": "monospace",
      "labelFontSize": 12, "titleFontSize": 13
    },
    "header": {
      "labelFont": "monospace", "titleFont": "monospace", "labelFontSize": 10
    },
    "legend": {
      "labelColor": "#cccccc", "titleColor": "#ffffff",
      "labelFont": "monospace", "titleFont": "monospace"
    },
    "mark": {"font": "monospace"}
  }
}
```

- `background` is a **top-level** property (not inside `config`).
- For light theme: use `"background": "#ffffff"`, label/title colors `"#333333"`.
- Include `header` (facet/small-multiple headers) and `mark` (text annotations) for complete font coverage.
- Use CSS generic families (`monospace`, `serif`, `sans-serif`) for cross-environment compatibility — named fonts like `Arial` may not be installed in the server-side rendering container.
- Omit font keys entirely to keep Vega-Lite's default.
- Add `"usermeta": {"merge": "extend"}` at the top level of the template JSON to use extend mode.
- Add `"usermeta": {"ui-merge": "none"}` to disable Snowsight UI theme adjustments and render the chart exactly as specified.

---

### C-alt · Colors via `vega_template` (without viz_policies)

Two deterministic approaches when viz_policies are not needed or not available:

**Exact value → hex mapping** with a `_color` calculate transform:

```json
{
  "transform": [
    {
      "calculate": "datum.STATUS === 'Active' ? '#22c55e' : datum.STATUS === 'Inactive' ? '#ef4444' : datum.STATUS === 'Pending' ? '#eab308' : ''",
      "as": "_color"
    }
  ],
  "encoding": {
    "color": {
      "field": "STATUS",
      "type": "nominal",
      "scale": { "range": { "field": "_color" } }
    }
  }
}
```

The `_color` transform fires for every chart. It only works correctly when the chart's color channel uses the same column referenced in the `calculate` expression. Only one column can be targeted per template.

**Pinned values with palette fallback** — pin key values and auto-assign the rest from a scheme:

```json
{
  "encoding": {
    "color": {
      "scale": {
        "domain": ["Furniture", "Technology", "Office Supplies"],
        "range": ["#4e79a7", "#f28e2b", "#e15759"],
        "scheme": "tableau10"
      }
    }
  },
  "usermeta": { "merge": "extend" }
}
```

Values not in `domain` get the next color from `scheme`. After assignment, `scheme` is removed from the final spec. Supported schemes: `tableau10`, `tableau20`, `category10`, `category20`, `category20b`, `category20c`, `dark2`, `paired`, `pastel1`, `pastel2`, `set1`, `set2`, `set3`, `accent`.

Use `extend` mode so the template adds colors without overwriting the LLM's `field` or `type`.

---

### C · Brand colors (`viz_policies` — `ensure_color`)

```json
{
  "name": "brand_colors",
  "rules": [{"column": "COMPANY", "role": "COLOR"}],
  "actions": [{"type": "ensure_color", "params": {
    "mapping": {"Snowflake": "#29B5E8", "Competitor": "#FF6B35"}
  }}]
}
```

- `column` = the column name as it appears in the chart encoding (case-insensitive). **Beware aliasing**: if the LLM generates `SUM(REVENUE) AS TOTAL`, the chart field is `TOTAL`, not `REVENUE`, and the rule won't match. Pin column aliases in your semantic view metrics, or use `role` alone (without `column`) to match by encoding position.
- `role`: `X_AXIS`, `Y_AXIS`, `COLOR`, `FILL`, `STROKE`, `SHAPE`, `SIZE`, `TOOLTIP`, `THETA` — omit to match any role.
- `ensure_color` uses a calculate transform; it fires deterministically regardless of LLM output. It checks all three channels (`color`, `fill`, `stroke`) and applies to whichever is present — but in rules, `COLOR`, `FILL`, and `STROKE` are distinct role values.
- Use `"rules": []` (empty) to apply brand colors to any chart regardless of which column is on the color channel. Use `"rules": [{"column": "X", "role": "COLOR"}]` to target a specific column.
- **Do NOT also set a static `domain`/`range` in `vega_template` for the same column** — the policy wins and makes the template entry dead code.
- If two policies both fire `ensure_color` on the same chart, the second silently overwrites the first's `_color` transform. Write mutually exclusive rules to avoid this.

---

### D · Per-value shapes (`viz_policies` — `ensure_shape`)

```json
{
  "name": "company_shapes",
  "rules": [{"column": "COMPANY", "role": "SHAPE"}],
  "actions": [{"type": "ensure_shape", "params": {
    "mapping": {"Snowflake": "diamond", "Competitor": "square"}
  }}]
}
```

Valid shape values: `circle`, `square`, `cross`, `diamond`, `triangle-up`, `triangle-down`, `triangle-right`, `triangle-left`, `star`.
The rule must include a `column` — `ensure_shape` needs it to build the calculate transform.
**Scatter plots only** — `ensure_shape` is silently skipped for all other mark types (`bar`, `line`, `area`, etc.).

---

### E · Number formatting (`viz_policies` — `ensure_number_format`)

```json
{
  "name": "dollar_revenue",
  "rules": [{"column": "REVENUE", "role": "Y_AXIS"}],
  "actions": [{"type": "ensure_number_format", "params": {
    "format": "$,.0f",
    "channel": "y"
  }}]
}
```

**Critical**: match `role` to `channel`. If `channel: "y"` then the rule must use `"role": "Y_AXIS"` (not just `"column": "REVENUE"` without a role) — otherwise the policy can match when the column is on X and incorrectly apply the format to Y.

Common D3 format strings:

| Intent | Format string |
|--------|--------------|
| Dollar, no decimals | `$,.0f` |
| Dollar, 2 decimals | `$,.2f` |
| Percentage (×100) | `.1%` |
| SI prefix (1.2M) | `.2s` |
| Fixed 2 decimals | `.2f` |
| Integer | `d` |

`channel` values — axis: `x`, `y`, `x2`, `y2` (writes `axis.format`); legend: `color`, `size`, `opacity` (writes `legend.format`). Omit `channel` to auto-apply to **all quantitative channels** in the spec. **Important**: when `channel` is omitted, temporal channels are skipped — always specify `channel` explicitly when formatting a date axis.

---

### F · Axis range / zero baseline (`viz_policies` — `ensure_axis_range`)

```json
{
  "name": "zero_baseline",
  "rules": [],
  "actions": [{"type": "ensure_axis_range", "params": {
    "channel": "y",
    "min": 0
  }}]
}
```

- Empty `rules: []` fires on every chart.
- Use `"min": 0` to prevent misleading y-axis truncation.
- Add `"max": 100` to clamp a percentage axis.
- `channel`: `"x"` or `"y"` (default: `"y"`).

---

### G · Sort order (`viz_policies` — `ensure_sort`)

**Custom list (fiscal quarters, weekdays, …):**
```json
{
  "name": "quarter_order",
  "rules": [{"column": "QUARTER", "role": "Y_AXIS"}],
  "actions": [{"type": "ensure_sort", "params": {
    "channel": "y",
    "custom_order": ["Q1", "Q2", "Q3", "Q4"]
  }}]
}
```

**Ascending / descending / data order:**
```json
{"type": "ensure_sort", "params": {"channel": "y", "order": "descending"}}
```

`order` options: `"ascending"`, `"descending"`, `"none"` (preserves SQL ORDER BY). `channel` default: `"y"`.
When `custom_order` is set, `order` is ignored.

**Note on quantitative axes**: `"order": "ascending"/"descending"` on a quantitative axis inverts the scale (flips the axis direction), it does NOT sort by value. For value-based bar sorting use `custom_order` or a `vega_template` with a sort object.

---

### H · Force chart type (`viz_policies` — `change_viz_type`)

```json
{
  "name": "revenue_bar",
  "rules": [
    {"column": "REVENUE", "role": "Y_AXIS"},
    {"viz_type": "bar", "negate": true}
  ],
  "actions": [{"type": "change_viz_type", "params": {"viz_type": "bar"}}]
}
```

`change_viz_type` is an **LLM action** — it triggers a regeneration prompt, not a deterministic transform. LLM actions are capped at **2 regeneration attempts** to prevent infinite loops.
The second rule (`negate: true`) prevents an infinite loop when the chart is already the right type.

---

### Temporal axis formatting tips

If date axes show `2012` or wrong months, the LLM likely set a `timeUnit` that discards date components. Vega-Lite re-anchors extracted components to epoch **January 1, 2012**, so `%Y` shows `2012` and `%m` shows `01`.

**Fix via `vega_template`** — null out `timeUnit` and set the format:
```json
{
  "encoding": {
    "x": {
      "timeUnit": null,
      "axis": { "format": "%b %d, %Y" }
    }
  }
}
```

**Rule of thumb**: if your data contains full date strings (`"2026-03-24"`), omit `timeUnit` entirely. Only use `timeUnit` for aggregation (e.g., `"utcyearmonth"` to aggregate to month).

D3 time-format strings (`%b`, `%Y`, etc.) only work when the field's Vega-Lite `type` is `"temporal"`. If the LLM typed it as `"ordinal"`, force `"type": "temporal"` in the template.

---

## Step 3: Generate and present the block

Assemble the parts into a complete block. Always:

1. Put any soft-instruction text **before** `vega_template:`.
2. Put `vega_template:` **before** `viz_policies:`.
3. Validate the JSON in `vega_template:` is syntactically valid.
4. Check that each `ensure_number_format` rule's `role` matches its `channel`.
5. Check that `ensure_color` and `vega_template` don't both define colors for the same field.

Then present the block with:
- Where to paste it (Agent `instructions.orchestration` or SM `custom_instructions`)
- A brief explanation of what each section does
- Any caveats (e.g., fonts require the typeface loaded in the host page)

---

## Common pitfalls to warn about

| Pitfall | Fix |
|---------|-----|
| `viz_policies:` before `vega_template:` in the block | Swap order — parser truncates at `viz_policies:` |
| `ensure_number_format` with `column` only (no `role`) + specific `channel` | Add matching `role` to the rule |
| `vega_template` color `domain`/`range` + `ensure_color` for same column | Remove the template color block; policy wins anyway |
| `ensure_sort` `order: "descending"` on quantitative axis to sort bars | Use `custom_order` or `vega_template` sort object |
| `ensure_shape` with no `column` in the rule | `ensure_shape` requires a `column` to build the transform |
| Custom font not loading | Use CSS generic families: `sans-serif`, `serif`, `monospace` |
| LLM aliases column (`SUM(REVENUE) AS TOTAL`) so rule on `REVENUE` won't match | Pin aliases in semantic view metrics, or use `role` alone without `column` |
| Two `ensure_color` policies fire on same chart | Second silently overwrites first's `_color` transform — use mutually exclusive rules |
| Typo in policy param name (e.g. `"chanell"`) | Silently uses default values — no user-visible warning; double-check param names |
| Setting `"mark": "line"` in agent-level `vega_template` | Forces every chart to a line — only override `mark` at the semantic view level |
| `background` placed inside `config` | Must be a **top-level** property, not inside `config` |

## Stopping Points

- After Step 1 if requirements are ambiguous
- After Step 3 to present the block for user review

**Resume rule:** After approval, apply changes without re-asking previous decisions.

## Output

Complete `<chart_customization>` block ready to paste into Agent or Semantic Model configuration.
