# The scene-spec contract

A video is authored as **data**, not markup. The spec is the artifact you edit, review and diff; the HTML,
webm and MP4 are all derived.

Anything that emits or renders this spec depends on its shape, so changing a type's shape is a breaking
change: add a new type rather than reshape one that specs already use.

## File shape

`<name>.scenes.json`:

```json
{ "brand": "<preset key>", "foot": "<footer text>", "scenes": [ { "type": "...", "dur": 4200, ... } ] }
```

A bare `[...]` array also works (brand then comes from `--brand`).

- **`*asterisks*`** around a word or phrase highlight it in the brand accent.
- **`_`** inside a phrase is a non-breaking space — stops a two-word term breaking across lines.
- `dur` is milliseconds. `TOTAL_S = sum(dur)/1000`; the renderer prints it, and that number is what you
  `ffprobe` the output against.

## Scene types

| Type | Shape | Use for |
|---|---|---|
| `hook` / `cta` | `{logo:true, lines:[..], sub}` | Open and close. Kinetic headline + brand lockup |
| `statement` | `{kicker, lines:[..], sub}` | A claim, held still long enough to read |
| `turn` | `statement` + `flow2:[{t,s},{t,s}]` | The pivot beat — collapses a flow to show what goes away |
| `flow` | `{kicker, lines, nodes:[{t,s}...], sub}` | Dashed pipeline diagram |
| `stat` | `{kicker, stat:{prefix,to,suffix,accent,label}, sub}` | Animated count-up. **`to` must be an integer** |
| `feature` | `{kicker, title, chips:[..], sub}` | What the thing is, as chips rather than prose |
| `arch` | `{kicker, boxes:[{t,s,tag}...], labels:[..], pills:[..]}` | Architecture diagram + overlay pills |
| `snowsight` | `{kicker, title, body:[{code}\|{grid}], callouts:[{text,pos}]}` | Faithful product-UI rendering |
| `domains` | `{kicker, lines, domains:[..]}` | A set of areas or systems |
| `cellgrid` | `{kicker, lines, cells:{cols,rows,blankPct,stayPct,seed,legend}, sub}` | Coverage/abstention as filling cells |
| `bars` | `{kicker, lines, bars:[{label,from,to,note}], sub}` | Before/after. Grey covers `from`, accent shows the delta |
| `funnel` | `{kicker, lines, stages:[{v,t,s}], narrow, sub}` | Narrowing stages. `v` counts up — integers |
| `branch` | `{kicker, lines, question, yes:{...}, no:{...}, sub}` | A decision with two outcomes |
| `meter` | `{kicker, lines, meter:{capPct,capLabel,haltLabel}, sub}` | Fills to a ceiling and halts |

Complete worked spec: `../engine/references/reference.scenes.json` (10 scenes, 43.7s).

## Rounding: which fields are safe

| Field | Rounded? | So |
|---|---|---|
| `stat.to`, `funnel.stages[].v` | **Yes** — `Math.round()` | Integers only |
| `bars[].from` / `.to` | No — drive pixel widths | Decimals fine |
| `sub`, `label`, `note`, `t`, `s` | No — static text | Put precision here |

## Brands

`../engine/assets/brands.json` carries presets: `snowflake` (add your own) — colours
plus logo filenames. Logos are real SVGs in `../engine/assets/`, inlined via `currentColor`.

- Paths in `brands.json` are **relative to the engine root** (`assets/x.svg`), because `render.mjs` does
  `path.join(ENGINE_ROOT, file)` — not relative to the assets dir.
- A preset with only one logo must set **`"logoLeft": ""`** or the `Your&nbsp;Brand` placeholder renders.
  The template does `Object.assign({}, DEFAULT_BRAND, window.__BRAND__)`, so an *absent* key inherits the
  placeholder while an empty string correctly suppresses it.
- Trim trailing whitespace out of a logo's `viewBox`, or it pads the `×` lockup gap.
- **Do not "minify" logo path data.** Rounding coordinates to 2dp corrupted the Snowflake mark so only 2
  of 6 arms rendered. Safe minification is stripping comments/`title`/`desc`/`id`/`version` and inter-tag
  whitespace only — leave every `d=` byte-identical.
- Inline SVG needs **both** `width` and `height` (or explicit CSS width). Height alone collapses it to
  zero width and it silently does not render.
- A CSS `mask` does **not** work for recolouring a logo — it resolves as luminance, so black paths become
  invisible. Inline the SVG and use `currentColor`.

## Authoring with a model

`claude-opus-5` spends a large hidden reasoning budget out of `max_tokens` and **returns an empty string
with no error** when starved. Use `max_tokens` ≥ 32000 (64000 costs nothing extra when the answer is
short). A spec/deck authoring pass measures 400–440s and ~38K completion tokens.

Then run `../gauntlet/copy_gate.py` on the result — a model will cheerfully write a 14-word headline and a
non-integer `stat.to`.

## See also

- `editing.md` — how many words, how many seconds, what shape
- `../gauntlet/copy_gate.py` — validates a spec before you spend a render on it
