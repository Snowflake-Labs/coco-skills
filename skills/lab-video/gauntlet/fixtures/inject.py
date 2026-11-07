"""Deliberate defects, injected into a known-good spec on demand.

    python3 ../copy_gate.py ../../engine/references/reference.scenes.json \
        --defect rounded_stat,placeholder_brand,internal_jargon,bloat

WHY A FIXTURE AND NOT A BROKEN SPEC FILE
  A checked-in broken spec rots: the gate changes, the fixture does not, and it keeps
  passing for reasons nobody rechecks. Mutating the CURRENT known-good spec means the
  fixture tracks the real one for free, and the diff between pass and fail is exactly the
  defect under test.

  This exists because a gate which has never failed is not known
  to work. `copy_gate.py`'s own first version FAILED a shipped video on thresholds set from
  intuition; the fix was to calibrate against real cases in both directions — known-good
  must pass, known-bad must fail.

Each defect maps to a numbered entry in ../../patterns/anti-patterns.md. Every one of them
reached a real video or a real preset.
"""
import copy


def _first(scenes, *types):
    for i, sc in enumerate(scenes):
        if sc.get("type") in types:
            return i, sc
    return (0, scenes[0]) if scenes else (None, None)


def rounded_stat(spec):
    """#3 — a measured figure counted up through Math.round().

    stat.to: 99.8 renders as 100. The defect is not the rounding, it is that rounding UP a
    measured figure manufactures a stronger claim than the evidence supports.
    """
    scenes = spec["scenes"]
    i, sc = _first(scenes, "stat", "meter")
    if sc.get("type") == "meter":
        sc.setdefault("meter", {})["capPct"] = 87.4
    else:
        sc.setdefault("stat", {})["to"] = 99.8
    return "scene %d: non-integer count-up target" % i


def placeholder_brand(spec):
    """#7 — a brand preset with no logo, so `Your&nbsp;Brand` renders on hook and CTA.

    The trap inside the trap: grepping the emitted HTML for the entity finds it every time,
    because it appears in the template's own DEFAULT_BRAND source line. The only real check
    is whether the injected brand carries a logoLeft key at all — which is why this defect
    points `brand` at a name that is not in brands.json.
    """
    spec["brand"] = "__missing_brand__"
    return "brand set to a key absent from brands.json"


def internal_jargon(spec):
    """#11 — internal vocabulary burned into a frame, where it cannot be redacted.

    Deliberately uses HARD jargon (no customer meaning) rather than the SOFT commercial
    words. `credits` and `run-rate` are legitimate in a cost conversation and the shipped
    reference trailer used them correctly; treating those as failures is what made the first
    gate reject known-good work.
    """
    scenes = spec["scenes"]
    i, sc = _first(scenes, "hook", "stat")
    sc["sub"] = "Whitespace propensity against quota attainment"
    return "scene %d: HARD jargon in `sub`" % i


def bloat(spec):
    """#12 — the two-minute video that should have been forty seconds.

    Two symptoms at once, because they co-occur: a second CTA (two asks is zero asks) and a
    runtime past the soft cap. Cutting is the highest-leverage edit available and the one
    least often taken.
    """
    scenes = spec["scenes"]
    cta = next((sc for sc in scenes if sc.get("type") == "cta"), None)
    extra = copy.deepcopy(cta) if cta else {"type": "cta", "lines": "Get in touch.",
                                            "dur": 4000}
    extra["lines"] = "Or reply to this email."
    mid = max(1, len(scenes) // 2)
    scenes.insert(mid, extra)
    for sc in scenes:
        sc["dur"] = int(sc.get("dur", 4000)) + 4000
    return "second cta at index %d, +4s on every beat" % mid


def missing_dur(spec):
    """A beat with no `dur` silently inherits the engine's 4000ms default.

    Not in the numbered list, but it is the quietest way for a spec to stop matching the
    TOTAL_S you then pass to probe.py --expect-sec.
    """
    scenes = spec["scenes"]
    i = min(1, len(scenes) - 1)
    scenes[i].pop("dur", None)
    return "scene %d: `dur` removed" % i


DEFECTS = {
    "rounded_stat": rounded_stat,          # anti-patterns #3
    "placeholder_brand": placeholder_brand,  # anti-patterns #7
    "internal_jargon": internal_jargon,    # anti-patterns #11
    "bloat": bloat,                        # anti-patterns #12
    "missing_dur": missing_dur,
}


def apply(spec, names):
    """Return a mutated deep copy. Unknown names raise rather than silently no-op.

    A fixture that quietly ignores a typo'd defect name reports a green gate for a defect
    that was never injected — the exact class of failure this module exists to catch.
    """
    out = copy.deepcopy(spec)
    if isinstance(out, list):
        out = {"scenes": out}
    unknown = [n for n in names if n not in DEFECTS]
    if unknown:
        raise SystemExit("unknown defect(s): %s\navailable: %s"
                         % (", ".join(unknown), ", ".join(sorted(DEFECTS))))
    applied = [DEFECTS[n](out) for n in names]
    print("injected  %s" % "; ".join(applied))
    return out
