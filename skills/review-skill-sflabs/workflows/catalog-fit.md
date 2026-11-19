# Catalog comparison

Reference documentation only. `scripts/checks.py` performs advisory name checks;
`scripts/catalog.py` produces a deterministic lexical shortlist using only
`references/catalog-snapshot.json` shipped with the reviewed image. No installed
skill directories, internal inventory, remote APIs or model calls are consulted.

The partial corpus remains exactly 26 previously selected public Labs names at
revision `89c57c211105bb5a870ce2f6e6e495a20c962fd8`. Only `rbac`, `snowpipe-bcdr`
and `semantic-view-patterns` have summaries/triggers enriched from their public
`skills/<name>/SKILL.md` frontmatter, read from that pinned local Git source.
Each enriched record cites its source path and revision. No additional names or
approvals are implied. The corpus is neither exhaustive nor current.

Ranking counts distinct shared lowercase ASCII tokens from candidate name,
description and summary against public name/summary/triggers, removing a fixed
small stop-word set. Results with zero overlap are omitted. Ties sort by stable
SHA-256 of `Snowflake-Labs/coco-skills/skills/<public-name>`; at most three IDs and
a fixed differentiation question are emitted. Scores, raw text, public summaries,
candidate labels and filenames are not emitted. IDs can be mapped by humans using
the trusted public snapshot; hashes are not anonymization. The host rejects IDs
outside its own pinned catalog. Updating the corpus changes its full provenance
digest and requires a compatible reviewed image/launcher pair.

The candidate directory's own name is excluded from related results and exact
self-collision, but this alone does not prove an update: PR added/updated/deleted/
renamed context comes only from trusted Git selection and explicit operator
mapping. Without that context the local reviewer makes no new-versus-update claim.
Missing catalog data is unavailable, not an empty-corpus proof of originality.
Related results are prompts for human differentiation, not semantic equivalence,
originality scores, acceptance, automatic rejection, or additional legal approval.
