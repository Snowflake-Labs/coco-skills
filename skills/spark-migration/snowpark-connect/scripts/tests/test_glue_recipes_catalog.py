"""Structural guards for the AWS Glue recipe catalog and its EWI registration.

The catalog was a single 434-line ``glue-recipes.md`` until it outgrew the
``skill-length-decomposition`` threshold and was split into an index plus two
sub-files. Nothing tested it before, so a split that dropped a recipe, duplicated
a heading, or left a dangling ``Gnn`` reference would have been invisible.

These tests pin the properties that make the split safe:

  * every ``Gnn`` heading appears EXACTLY ONCE across the three files
    (``no-content-duplication-across-skills``);
  * the index carries a routing row for every ``Gnn`` that lives in a sub-file,
    so an existing reference of the form "glue-recipes.md recipe G3" still
    resolves through the index;
  * every relative link between the files points at a file that exists;
  * every ``glue_*`` recipe on disk is listed in the index's Phase 0.5 coverage
    table, and vice versa;
  * every ``SPRKCNTPY36xx`` code named in the catalog is registered in BOTH
    ``ewi_code_mapping.csv`` and ``ewi-codes.md``;
  * no file exceeds the decomposition threshold that forced the split.

Run from the ``snowpark-connect/`` directory:
    pytest scripts/tests/test_glue_recipes_catalog.py
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

_SKILL = Path(__file__).resolve().parents[2]
_REF = _SKILL / "references" / "python"
_RECIPES = _SKILL / "scripts" / "recipes"

_INDEX = _REF / "glue-recipes.md"
_SUBFILES = (
    _REF / "glue-recipes-transforms.md",
    _REF / "glue-recipes-types.md",
    _REF / "glue-recipes-io.md",
)
_ALL = (_INDEX, *_SUBFILES)

# ``## G12 — ...`` / ``## G3 — ...``
_HEADING = re.compile(r"^## (G\d+)\b", re.M)
# a routing-table row naming a recipe id in the first cell
_ROUTE_ROW = re.compile(r"^\|\s*\**(G\d+)\**\s*\|", re.M)
_CODE = re.compile(r"SPRKCNTPY36\d\d")
_MD_LINK = re.compile(r"\]\((glue-recipes[a-z-]*\.md)[^)]*\)")

# The decomposition threshold that forced the split in the first place.
_MAX_LINES = 300


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _headings(p: Path) -> list[str]:
    return _HEADING.findall(_text(p))


def test_all_catalog_files_exist():
    for p in _ALL:
        assert p.exists(), f"missing catalog file: {p.name}"


def test_every_recipe_heading_appears_exactly_once():
    seen: dict[str, list[str]] = {}
    for p in _ALL:
        for g in _headings(p):
            seen.setdefault(g, []).append(p.name)
    dupes = {g: where for g, where in seen.items() if len(where) > 1}
    assert not dupes, f"Gnn heading duplicated across files: {dupes}"


def test_heading_set_equals_the_routing_table_set():
    """The catalog's sections and its routing table must describe the SAME set.

    This replaced a contiguous-numbering check. Contiguity is the wrong invariant:
    the catalog is grown by stacked PRs, so a mid-stack state legitimately holds
    G1-G13 plus G20-G21 while G14-G19 are still in review, and a gap check fails
    on a perfectly correct tree. Set equality is strictly STRONGER for what the
    check is actually for — it catches a section silently dropped during a move
    (routed but no heading) AND a section left stranded (heading but unrouted) —
    and it stays true at every commit in the stack.
    """
    headings = {g for p in _ALL for g in _headings(p)}
    routed = set(_ROUTE_ROW.findall(_text(_INDEX)))
    assert headings, "no Gnn headings found at all"
    assert "G1" in headings, "catalog does not contain G1"
    only_heading = sorted(headings - routed, key=lambda g: int(g[1:]))
    only_routed = sorted(routed - headings, key=lambda g: int(g[1:]))
    assert not only_heading, f"section(s) present but not in the routing table: {only_heading}"
    assert not only_routed, f"routing table names section(s) that do not exist: {only_routed}"


def test_index_routes_every_recipe_that_lives_in_a_subfile():
    """A pre-existing reference says "glue-recipes.md recipe G3". G3 now lives in
    a sub-file, so the index MUST route it or that reference dangles."""
    routed = set(_ROUTE_ROW.findall(_text(_INDEX)))
    in_index = set(_headings(_INDEX))
    elsewhere = {g for p in _SUBFILES for g in _headings(p)}
    unrouted = sorted(elsewhere - routed, key=lambda g: int(g[1:]))
    assert not unrouted, (
        f"recipe(s) live in a sub-file but are not in the index routing table: "
        f"{unrouted}"
    )
    # Anything routed must actually exist somewhere.
    orphans = sorted(routed - (in_index | elsewhere), key=lambda g: int(g[1:]))
    assert not orphans, f"routing table names recipe(s) with no heading: {orphans}"


def test_relative_links_between_catalog_files_resolve():
    for p in _ALL:
        for target in set(_MD_LINK.findall(_text(p))):
            assert (_REF / target).exists(), f"{p.name} links to missing {target}"


def test_every_glue_recipe_on_disk_is_listed_in_the_coverage_table():
    on_disk = {
        d.name for d in _RECIPES.iterdir()
        if d.is_dir() and d.name.startswith("glue_") and (d / "recipe.py").exists()
    }
    assert on_disk, "no glue_* recipes found on disk"
    index = _text(_INDEX)
    missing = sorted(n for n in on_disk if n not in index)
    assert not missing, (
        f"glue_* recipe(s) on disk but absent from the index's Phase 0.5 coverage "
        f"table: {missing}"
    )


def test_coverage_table_names_no_recipe_that_does_not_exist():
    on_disk = {d.name for d in _RECIPES.iterdir() if d.is_dir()}
    named = set(re.findall(r"`(glue_[a-z0-9_]+)`", _text(_INDEX)))
    ghosts = sorted(named - on_disk)
    assert not ghosts, f"index names glue_* recipe(s) that do not exist: {ghosts}"


def test_recipe_verb_suffix_matches_the_contract():
    """The ``_rewrite`` / ``_annotate`` suffix IS the contract, and
    ``build_recipe_resolved_panel`` classifies the edit by it."""
    bad = [
        d.name for d in _RECIPES.iterdir()
        if d.is_dir() and d.name.startswith("glue_")
        and (d / "recipe.py").exists()
        and not d.name.endswith(("_rewrite", "_annotate"))
    ]
    assert not bad, f"glue_* recipe(s) with a non-contract suffix: {bad}"


@pytest.mark.parametrize("path", _ALL, ids=lambda p: p.name)
def test_catalog_files_are_under_the_decomposition_threshold(path: Path):
    n = len(_text(path).splitlines())
    assert n <= _MAX_LINES, (
        f"{path.name} is {n} lines (> {_MAX_LINES}); it was split because of the "
        f"skill-length-decomposition rule — split again rather than growing it"
    )


def test_every_glue_code_in_the_catalog_is_registered_in_the_csv():
    csv_path = _SKILL / "scripts" / "data" / "python" / "ewi_code_mapping.csv"
    with csv_path.open(encoding="utf-8") as f:
        registered = {r["ewi_code"] for r in csv.DictReader(f)}
    named = {c for p in _ALL for c in _CODE.findall(_text(p))}
    assert named, "catalog names no SPRKCNTPY36xx codes at all"
    missing = sorted(named - registered)
    assert not missing, f"code(s) in the catalog but not in ewi_code_mapping.csv: {missing}"


def test_every_glue_code_in_the_catalog_is_documented_in_ewi_codes_md():
    documented = set(_CODE.findall(_text(_REF / "ewi-codes.md")))
    named = {c for p in _ALL for c in _CODE.findall(_text(p))}
    missing = sorted(named - documented)
    assert not missing, f"code(s) in the catalog but not in ewi-codes.md: {missing}"


def test_live_validation_claim_is_scoped_and_not_inherited_by_new_recipes():
    """#3894 validated G1-G12 on SCOS against live Glue output. G13+ were derived
    from source and have NOT run against Snowflake. The index must not state the
    validation claim unscoped, or G13+ silently inherit it."""
    index = _text(_INDEX)
    assert "validated on SCOS" in index, "the validation claim disappeared entirely"
    # The sentence must name the range it applies to.
    claim_para = next(
        (para for para in index.split("\n\n") if "validated on SCOS" in para), ""
    )
    assert "G1" in claim_para and "G12" in claim_para, (
        "the 'validated on SCOS' claim is not scoped to a recipe range — G13+ "
        "would inherit a live-validation claim they do not have"
    )
    assert "NOT live-verified" in claim_para or "not live-verified" in claim_para, (
        "the claim paragraph does not say that the later recipes are unverified"
    )


# ---------------------------------------------------------------------------
# The docs that POINT AT the catalog must agree with it.
#
# Added after an AI reviewer caught what these tests missed: fix-rules.md's
# required-reading list said the transforms sub-file covered G9 and that there
# were "two" sub-files, while the routing table routed G9 to a third file that
# the list never mentioned. Every assertion above passed, because they only
# checked the catalog's internal consistency -- never the skill docs whose whole
# job is to send the fixer to the right file. A fixer hitting `awsglue.gluetypes`
# would never have been told to read it.
# ---------------------------------------------------------------------------
_SKILL_DOCS = (
    _SKILL / "migrate-pyspark-to-snowpark-connect" / "references" / "fix-rules.md",
    _SKILL / "migrate-pyspark-to-snowpark-connect" / "agents" / "fixer.md",
)


@pytest.mark.parametrize("doc", _SKILL_DOCS, ids=lambda p: p.name)
def test_skill_docs_name_every_catalog_subfile(doc: Path):
    """A doc that routes the fixer to the catalog must name EVERY sub-file.

    Naming only some of them silently strands whichever recipes live in the
    unnamed one -- the reader is never told to open it.
    """
    named = set(re.findall(r"(glue-recipes-[a-z]+\.md)", _text(doc)))
    expected = {p.name for p in _SUBFILES}
    missing = sorted(expected - named)
    assert not missing, (
        f"{doc.name} routes to the Glue catalog but never names {missing}; a fixer "
        f"reaching those recipes would not be told to read them"
    )


@pytest.mark.parametrize("doc", _SKILL_DOCS, ids=lambda p: p.name)
def test_skill_docs_do_not_misattribute_a_recipe_to_the_wrong_subfile(doc: Path):
    """Where a skill doc claims 'file X covers G3, G4, ...', those ids must match
    where the sections actually live. This is the specific error the reviewer
    caught: G9 attributed to transforms while it lives in types."""
    text = _text(doc)
    actual = {}
    for sub in _SUBFILES:
        for g in _headings(sub):
            actual[g] = sub.name
    # Find each "<subfile>.md ( ... G-ids ... )" style claim and check it.
    for sub in _SUBFILES:
        for m in re.finditer(re.escape(sub.name) + r"`?\s*\(([^)]*)\)", text):
            claimed = re.findall(r"\bG\d+\b", m.group(1))
            wrong = [g for g in claimed if g in actual and actual[g] != sub.name]
            assert not wrong, (
                f"{doc.name} says {sub.name} covers {wrong}, but "
                f"{ {g: actual[g] for g in wrong} } is where they actually live"
            )
