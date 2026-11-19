#!/usr/bin/env python3
"""Tier 1-2 gate for a scene spec. Pure stdlib: no render, no ffmpeg, no browser.

Runs in under a second, so it belongs BEFORE you spend a render and an encode on a spec
that was never going to ship. Every check exists because the corresponding mistake
reached a real video — see ../patterns/anti-patterns.md for the incident behind each.

    python3 copy_gate.py ../engine/references/reference.scenes.json
    python3 copy_gate.py spec.json --strict-jargon --strict-dashes
    python3 copy_gate.py fixtures/flawed.scenes.json --defect rounded_stat

Exit 0 on pass or warnings only. Exit 1 on FAIL. (Convention: a harness that
exits non-zero on warnings gets wrapped in `|| true` within a day.)
"""
import argparse, json, pathlib, re, sys

# Prose the viewer reads. `body` (product-UI code/grid rows) and `callouts` are
# deliberately exempt: they are verbatim product output, and forcing house style onto
# them would make a `snowsight` scene unfaithful, which is the one thing it must not be.
PROSE_KEYS = {"lines", "sub", "kicker", "title", "question", "label", "t", "s",
              "edge", "capLabel", "haltLabel", "chips", "domains", "note"}
EXEMPT_KEYS = {"body", "callouts"}

# Internal vocabulary. Once burned into a frame it cannot be redacted.
#
# TWO TIERS, because a single list was miscalibrated: it failed the shipped reference
# Postgres Mirroring trailer on "credits" and "run-rate". That video went through an
# explicit honesty review and those words were correct in it — a customer buys credits
# and knows what a run-rate is. A gate that reddens on known-good work gets ignored.
#
# HARD = no customer meaning, or reveals internal strategy. Always FAIL.
# SOFT = legitimate commercial vocabulary: right in a cost conversation, an unwanted
#        upsell signal in a capability pitch. WARN, or FAIL under --strict-jargon.
HARD_JARGON = [r"\bquota\b", r"\bcommission\b", r"\bexpansion value\b", r"\bpropensity\b",
               r"\bwhitespace\b", r"\bACV\b", r"\bseed users?\b", r"\battainment\b",
               r"[\d.]+\s*x\s*(?:workload\s*)?lift", r"modelled workload lift",
               r"\bland and expand\b", r"\bchurn risk score\b"]
SOFT_JARGON = [r"\bcredits?\b", r"\bconsumption\b", r"run-?rate", r"\btiers?\b",
               r"\bbaseline\b"]

# Counting up a rounded figure manufactures a stronger claim than the evidence.
INTEGER_FIELDS = [("stat", "to"), ("meter", "capPct")]

# Measured across 34 scenes from three shipped videos:
#
#   HEADLINE (`lines`)          p50 4   p90 6   max 6     <- tight, real, gateable
#   READ (+sub/title/question)  p50 12  p90 23  max 32    <- context only
#   TOTAL (+diagram labels)     p50 20  p90 36  max 47    <- context only
#
# So the "few words per beat" rule lives on the HEADLINE. Counting every token on the
# frame instead rejected 8 of 10 scenes of a video that shipped and worked, because a
# diagram legitimately carries labels that are scanned rather than read.
# The FAIL threshold sits ABOVE the worst good case (6), not at the ideal.
HEADLINE_KEYS = {"lines"}
READ_KEYS = {"lines", "sub", "title", "question"}
LABEL_KEYS = {"kicker", "t", "s", "chips", "domains", "note", "label", "edge",
              "capLabel", "haltLabel"}


def walk_prose(node, out, key=None, keys=None):
    keys = keys if keys is not None else PROSE_KEYS
    if isinstance(node, dict):
        for k, v in node.items():
            if k in EXEMPT_KEYS:
                continue
            walk_prose(v, out, k, keys)
    elif isinstance(node, list):
        for v in node:
            walk_prose(v, out, key, keys)
    elif key in keys and node is not None:
        out.append(str(node))


def count_words(sc, keys):
    texts = []
    walk_prose(sc, texts, keys=keys)
    blob = " ".join(texts).replace("_", " ").replace("*", "")
    return len([w for w in re.split(r"\s+", blob) if w.strip()])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--brands", default=None,
                    help="brands.json (default: ../engine/assets/brands.json)")
    ap.add_argument("--max-headline", type=int, default=8,
                    help="FAIL past this many words in `lines` (shipped ceiling: 6)")
    ap.add_argument("--max-total", type=float, default=120.0, help="hard runtime cap, seconds")
    ap.add_argument("--soft-total", type=float, default=45.0, help="warn past this")
    ap.add_argument("--min-beat", type=float, default=2.5)
    ap.add_argument("--max-beat", type=float, default=14.0)
    ap.add_argument("--allow-jargon", action="store_true", help="internal-audience video")
    ap.add_argument("--strict-jargon", action="store_true",
                    help="promote commercial vocabulary (credits, run-rate) from WARN to FAIL")
    ap.add_argument("--strict-dashes", action="store_true",
                    help="house rule: no em/en dashes in prose")
    ap.add_argument("--defect", default="",
                    help="fixture only: comma list of defects to inject")
    a = ap.parse_args()

    path = pathlib.Path(a.spec)
    spec = json.loads(path.read_text())
    if isinstance(spec, list):
        spec = {"scenes": spec}

    if a.defect:
        from importlib import import_module
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "fixtures"))
        spec = import_module("inject").apply(spec, a.defect.split(","))
    scenes = spec.get("scenes", [])

    fails, warns = [], []

    def fail(m): fails.append(m)
    def warn(m): warns.append(m)

    if not scenes:
        fail("spec has no scenes")

    # --- runtime budget -------------------------------------------------------
    durs = []
    for i, sc in enumerate(scenes):
        d = sc.get("dur")
        if not isinstance(d, (int, float)) or d <= 0:
            fail(f"scene {i} ({sc.get('type','?')}): missing or invalid `dur` "
                 f"(silently becomes the engine's 4000ms default)")
            continue
        durs.append(d / 1000.0)
        if d / 1000.0 < a.min_beat:
            warn(f"scene {i} ({sc.get('type','?')}): {d/1000:.1f}s is under {a.min_beat}s — unreadable")
        if d / 1000.0 > a.max_beat:
            warn(f"scene {i} ({sc.get('type','?')}): {d/1000:.1f}s is over {a.max_beat}s — dead air")
    total = sum(durs)
    if total > a.max_total:
        fail(f"total runtime {total:.1f}s exceeds the {a.max_total:.0f}s cap")
    elif total > a.soft_total:
        warn(f"total runtime {total:.1f}s is past {a.soft_total:.0f}s — "
             f"is every beat earning its seconds?")

    if durs and durs[0] > 5.0:
        warn(f"hook resolves at {durs[0]:.1f}s — the scroll decision is made inside 5s")

    # --- one idea, few words in the headline ----------------------------------
    for i, sc in enumerate(scenes):
        h = count_words(sc, HEADLINE_KEYS)
        if h > a.max_headline:
            fail(f"scene {i} ({sc.get('type','?')}): {h}-word headline, limit {a.max_headline} "
                 f"— no shipped video exceeds 6")
        elif h > 6:
            warn(f"scene {i} ({sc.get('type','?')}): {h}-word headline is above the measured "
                 f"ceiling of 6 in shipped work")

    # --- rounding -------------------------------------------------------------
    for i, sc in enumerate(scenes):
        for obj_key, num_key in INTEGER_FIELDS:
            obj = sc.get(obj_key)
            if isinstance(obj, dict) and num_key in obj:
                v = obj[num_key]
                if isinstance(v, float) and not float(v).is_integer():
                    fail(f"scene {i}: {obj_key}.{num_key}={v} is not an integer — the count-up "
                         f"rounds it, so it renders as {round(v)}. Put decimals in `sub`/`label`.")
        for st in sc.get("stages", []) or []:
            v = st.get("v")
            if isinstance(v, float) and not float(v).is_integer():
                fail(f"scene {i}: funnel stage v={v} is not an integer — counts up, will round")

    # --- one ask --------------------------------------------------------------
    ctas = [i for i, sc in enumerate(scenes) if sc.get("type") == "cta"]
    if len(ctas) > 1:
        fail(f"{len(ctas)} `cta` scenes at {ctas} — two asks is zero asks")
    if not ctas:
        warn("no `cta` scene — deliberate for a talk-track bed, a defect for a trailer")

    # --- vocabulary -----------------------------------------------------------
    texts = []
    walk_prose(spec, texts)
    texts.append(str(spec.get("foot", "")))
    blob = "\n".join(texts)
    if not a.allow_jargon:
        for pat in HARD_JARGON:
            m = re.search(pat, blob, re.I)
            if m:
                fail(f"internal vocabulary on screen: \"{m.group(0)}\" — no customer meaning, "
                     f"cannot be redacted once rendered")
        for pat in SOFT_JARGON:
            m = re.search(pat, blob, re.I)
            if m:
                msg = (f"commercial vocabulary on screen: \"{m.group(0)}\" — correct in a cost "
                       f"conversation, an upsell signal in a capability pitch. Deliberate?")
                fail(msg) if a.strict_jargon else warn(msg)
    if a.strict_dashes:
        for ch, lbl in (("\u2014", "em-dash"), ("\u2013", "en-dash")):
            if ch in blob:
                warn(f"{lbl} in prose (house style)")

    # --- brand lockup ---------------------------------------------------------
    bpath = pathlib.Path(a.brands) if a.brands else \
        pathlib.Path(__file__).resolve().parent.parent / "engine" / "assets" / "brands.json"
    bkey = spec.get("brand")
    if bkey and bpath.exists():
        brands = json.loads(bpath.read_text())
        b = brands.get(bkey)
        if b is None:
            fail(f"brand \"{bkey}\" is not in {bpath.name} — DEFAULT_BRAND placeholder will render")
        else:
            # The template does Object.assign({}, DEFAULT_BRAND, __BRAND__), so an ABSENT
            # logoLeft inherits the `Your&nbsp;Brand` placeholder while an empty string
            # correctly suppresses it. Checking the emitted HTML for the entity does NOT
            # work: it appears in the template's own DEFAULT_BRAND source line every time.
            if not (bool(b.get("logoLeftFile")) or b.get("logoLeft") == ""):
                fail(f"brand \"{bkey}\" has neither `logoLeftFile` nor an explicit "
                     f"`\"logoLeft\": \"\"` — the literal `Your&nbsp;Brand` placeholder renders "
                     f"on hook/cta, and grepping for \"Your Brand\" will NOT find it.")
            for fk in ("logoLeftFile", "logoRightFile"):
                fn = b.get(fk)
                # render.mjs does path.join(ENGINE_ROOT, fn) and brands.json stores
                # "assets/x.svg", so resolve against the engine root, NOT the assets dir.
                if fn and not (bpath.parent.parent / fn).exists():
                    fail(f"brand \"{bkey}\": {fk}=\"{fn}\" not found under {bpath.parent.parent}")
    elif bkey:
        warn(f"cannot resolve brands.json at {bpath} — brand lockup unchecked")

    # --- report ---------------------------------------------------------------
    hl = [count_words(s, HEADLINE_KEYS) for s in scenes]
    rd = [count_words(s, READ_KEYS) for s in scenes]
    tt = [count_words(s, READ_KEYS | LABEL_KEYS) for s in scenes]
    print(f"spec      {path}")
    print(f"scenes    {len(scenes)}   runtime {total:.1f}s   "
          f"avg beat {(total/len(durs) if durs else 0):.1f}s")
    print(f"words     headline max {max(hl, default=0)} (gate {a.max_headline})   "
          f"read max {max(rd, default=0)}   on-frame max {max(tt, default=0)}   "
          f"[shipped anchors 6 / 32 / 47]")
    for m in warns:
        print(f"WARN  {m}")
    for m in fails:
        print(f"FAIL  {m}")
    print(f"---\n{len(fails)} fail, {len(warns)} advisory")
    if not fails:
        print("Tier 1-2 clear on the spec. That is permission to render and LOOK at it, "
              "not a result — see README.md.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
