# Substrate: what the pixels actually are

Three substrates. The choice is not aesthetic — it is determined by **what you are claiming**.

| Substrate | Claim it can support | Reproducible? | Fails when |
|---|---|---|---|
| **Synthetic motion graphics** | "here is an idea / a number / an argument" | Yes — re-render from spec | You need to prove software exists |
| **Captured product footage** | "this runs, here it is running" | Mostly — same app, same data | The app resists automation, or needs auth |
| **Human screen recording** | "this runs" (weakest provenance) | No | You need to regenerate it after a data change |
| **3D rendered footage** (Blender) | "here is the object, the space, the flow", shown spatially | Yes — re-render from the `bpy` script | You need to prove software runs, or the model is not the real thing |

## The decision rule

> **If the claim is "here is an idea", synthesise it. If the claim is "this works", film it.**

Everything else follows. A synthetic sequence is cheaper, sharper and infinitely re-renderable, and it
is the right answer for a proposal, a launch teaser or a broadcast asset. It is the *wrong* answer the
moment the video's job is to be evidence.

## The honesty seam, and it is narrow

The engine has a `snowsight` scene type that renders product UI faithfully — worksheet chrome, result
grids, monospace. It is genuinely useful: it shows what the customer will see without needing an
account, data, or a working deployment.

It is also the one scene type that can lie by construction, because a faithful rendering of a UI is
indistinguishable from a screenshot of that UI to a viewer.

The rule that has held: **a rendering may depict a product surface, but the video must not imply the
run happened.** In the shipped reference proposal trailer the account had zero Postgres instances and
zero `APPLY_MIRROR` runs, so the worksheet scene was authored as a rendering and described as one.
That was a deliberate decision, recorded, not an oversight.

Corollary: if a figure on screen was measured on a demo or labelled dataset, caption it as such **on
the scene itself**, not in the email. The email is not what gets forwarded.

## Hybrid is usually correct

The strongest shape observed is a synthetic sting plus captured body:

```
[synthetic]  logo / hook / one-line premise        3-6s
[captured]   the thing actually doing the thing    15-40s
[synthetic]  the number, the CTA, the lockup       6-10s
```

Synthetic frames are where you control the type, the pacing and the brand. Captured frames are where
the credibility is. In motion mode a filmed take is a `footage` scene between code scenes.

## When capture is blocked — and it often is

Automated capture can fail for several different reasons; diagnose the current environment rather
than promoting a historical failure into a permanent product limitation:

- **Component bootstrap/transport failures.** Earlier Streamlit captures stalled; a later authenticated
  CDP session exposed the hosted component successfully. Mounted UI and a saved result still do not
  prove a fresh query worked. Check each claim separately.
- **Auth walls.** A Snowsight or public SPCS endpoint requires a real session. Your own SPCS services
  are the exception — their internal DNS needs no auth at all, which is why capturing your own
  deployed app is tractable and capturing the customer's is not.
- **Browser/sandbox restrictions.** Test the permitted browser path. Installed Chrome through an
  isolated Playwright context handled loopback capture in the 2026-09-21 run when the bundled browser
  failed. Do not assume every client has the same restrictions or bypass an access control.

When capture is blocked, say so and fall back to a human screen recording. Do **not** quietly
substitute a synthetic mockup for footage you could not obtain — that converts a tooling limitation
into a false claim.

## Serve over HTTP, not file://

ES module imports are blocked over `file://` — the origin is `null`, so the browser refuses the import
and the page renders blank with no obvious error. Any capture or render harness pointed at a
module-based page must start a local HTTP server first. Motion mode's renderer does
this (`motion/lib/serve.mjs`); the engine's own `templates/trailer.html` avoids the problem by having no imports at all.

## See also

- `capture.md` — the mechanics and the version traps
- `../engine/` — the synthetic substrate's implementation
- `motion-mode.md` — a fourth way in: film a real page frame by frame on a virtual clock, so a deck,
  dashboard or app becomes footage that is reproducible like synthesis and true like capture
- `anti-patterns.md` — "the fabricated product surface"
