# Motion: how a beat animates

General web-animation doctrine (easing, durations, when not to animate) applies unchanged. What follows
is only what changes **because it is a video** rather than a page.

## Four inversions from web motion

**1. Reduced motion is not a fallback — it is your still-frame renderer.**

On the web, `prefers-reduced-motion` is an accessibility path. In a video render, honouring it would
produce a video with no motion. So the render path ignores it.

But it is the most useful verification tool you have: headless Chrome with
`--force-prefers-reduced-motion` lands every animation in its final state, so a screenshot shows what
a beat *resolves to* rather than catching it mid-transition.

**2. No interaction, so no interruptible motion.**

Springs and gesture physics exist to feel responsive to input. A video has no input. Deterministic
eased tweens are strictly better: reproducible frame-for-frame, which is what lets you re-render after
a copy fix and get an identical video apart from the fix.

**3. Continuous ambient loops fight the frame budget for nothing.**

A background that drifts forever costs encode bitrate on every frame and communicates nothing. Prefer
motion that *resolves*: a reveal, a count-up, a fill. One hero motion per beat, then hold.

**4. The clock is not the browser's clock.**

Headless Chrome's `--virtual-time-budget` advances virtual time but **freezes
`requestAnimationFrame` timestamps**, so anything driven off rAF deltas sits at frame zero. The
observed symptom is a headline count-up screenshotting as `0` — a zero where a real measured number
belongs, which is worse than an obviously broken frame because it looks intentional.

The fix: give every count-up a `setTimeout` fallback that lands the final value regardless of rAF, so
a stalled clock produces the *right* number rather than zero. The engine's template does this. New
animated primitives should too.

## SVG motion traps

- **`transform-box: fill-box`** is required to scale an SVG `rect` about its own box. Without it a bar
  animating `scaleY` renders displaced below the axis — geometry correct, position wrong.
- **SVG `id`s are document-wide.** Several inline SVGs each defining `<linearGradient id="g">` all
  resolve to the *first* definition; per-element variety silently collapses, and a shape referencing
  `url(#missing)` fills with nothing and reads as "no data". Namespace every id with a seed.

## Determinism is a feature

Anything random must run off a seeded PRNG. The engine's `cellgrid` does, so a re-render after a copy
change is pixel-identical apart from the copy. Without it you cannot answer "did my fix change
anything else?", so every review becomes a fresh full review.

## Type, not decoration

Kinetic type carries almost all the communicative load. Stagger characters or words on entry, ease-out,
then **let the type hold still while it is being read**. The most common failure is animating *during*
the readable window: eyes track movement instead of parsing words, and a 5-word line becomes
unreadable at 4 seconds.

No external fonts — the engine inlines everything, so the HTML renders identically offline and in a
container with no network.

## See also

- `editing.md` — pacing, beat structure, the measured numbers
- `../gauntlet/contactsheet.py` — sample frames and inspect resolved states
