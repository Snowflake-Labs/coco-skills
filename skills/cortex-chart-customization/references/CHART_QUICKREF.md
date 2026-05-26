# Chart Customization — Quick Reference

Cortex charts can be customized at two levels: **Agent** (applies to every chart) and **Semantic Model** (overrides the agent for a specific dataset). Both use the same `<chart_customization>` block, which is stripped before the LLM sees it so raw JSON or policy arrays never pollute the model's context.

---

## Where to put it

| Level | Field | Scope |
|---|---|---|
| Agent | `instructions.orchestration` | Global baseline — all charts |
| Semantic Model | `custom_instructions` or `module_custom_instructions.sql_generation` | Overrides agent for that SM only |

```xml
<chart_customization>
  ... soft instructions, vega_template:, viz_policies: ...
</chart_customization>
```

---

## Three content types

### 1 · Soft instructions
Plain text hint for the LLM — no hard guarantee.
```xml
<chart_customization>
Prefer bar charts for comparisons. Use short axis labels.
</chart_customization>
```

### 2 · `vega_template:`
Partial Vega-Lite JSON deep-merged onto every generated spec. Default mode `override` (template wins); add `"usermeta": {"merge": "extend"}` to only fill in missing values.
```xml
<chart_customization>
vega_template:
{"background": "#1a1a2e"}
</chart_customization>
```
→ [Vega editor](https://vega.github.io/editor/) · [Color schemes](https://vega.github.io/vega/docs/schemes/)

### 3 · `viz_policies:`
Rule-based enforcement. Rules use AND logic; mechanical actions apply deterministically, LLM actions trigger a spec regeneration.
```xml
<chart_customization>
viz_policies:
[{"name": "brand", "rules": [{"column": "TICKER", "role": "COLOR"}],
  "actions": [{"type": "ensure_color", "params": {"mapping": {"SNOW": "#29B5E8"}}}]}]
</chart_customization>
```

---

## Mechanical actions

| Action | Key params | Effect |
|---|---|---|
| `ensure_color` | `mapping: {"val": "#hex"}` | Per-value colors via calculate transform |
| `ensure_shape` | `mapping: {"val": "diamond"}` | Per-value point shapes |
| `ensure_sort` | `channel`, `order` (`ascending`/`descending`/`none`), `custom_order: [...]` | Forces sort on an encoding channel |
| `ensure_number_format` | `format` (D3), `channel` (optional — omit to apply to all quantitative) | Sets `axis.format` or `legend.format` |
| `ensure_axis_range` | `channel`, `min`, `max` | Sets `scale.domainMin` / `scale.domainMax` |

---

## Rule fields

```jsonc
{"column": "REVENUE", "role": "Y_AXIS", "viz_type": "bar", "negate": false}
// column: match when this column appears in encoding (empty = any)
// role:   COLOR | FILL | STROKE | SHAPE | SIZE | TOOLTIP | THETA | X_AXIS | Y_AXIS  (empty = any)
// viz_type: match chart mark type                     (empty = any)
// negate: invert the condition
```

---

## Complete example

Agent instructions with a dark theme, brand colors, dollar formatting, and a zero-baseline guarantee:

```xml
You are a helpful data analyst.
<chart_customization>
Always use concise axis titles.

vega_template:
{
  "background": "#1a1a2e",
  "config": {
    "title": {"font": "monospace", "fontSize": 16, "fontWeight": "bold", "color": "#ffffff"},
    "axis": {
      "labelColor": "#cccccc", "titleColor": "#ffffff",
      "labelFont": "monospace", "titleFont": "monospace",
      "labelFontSize": 12, "titleFontSize": 13
    },
    "header": {"labelFont": "monospace", "titleFont": "monospace", "labelFontSize": 10},
    "legend": {
      "labelColor": "#cccccc", "titleColor": "#ffffff",
      "labelFont": "monospace", "titleFont": "monospace"
    },
    "mark": {"font": "monospace"}
  }
}

viz_policies:
[
  {
    "name": "zero_baseline",
    "rules": [],
    "actions": [{"type": "ensure_axis_range", "params": {"channel": "y", "min": 0}}]
  },
  {
    "name": "dollar_revenue",
    "rules": [{"column": "REVENUE", "role": "Y_AXIS"}],
    "actions": [{"type": "ensure_number_format", "params": {"format": "$,.0f", "channel": "y"}}]
  },
  {
    "name": "brand_colors",
    "rules": [{"column": "COMPANY", "role": "COLOR"}],
    "actions": [{"type": "ensure_color", "params": {"mapping": {"Snowflake": "#29B5E8", "Competitor": "#FF6B35"}}}]
  },
  {
    "name": "force_bar_for_revenue",
    "rules": [{"column": "REVENUE", "role": "Y_AXIS"}, {"viz_type": "bar", "negate": true}],
    "actions": [{"type": "change_viz_type", "params": {"viz_type": "bar"}}]
  }
]
</chart_customization>
```
