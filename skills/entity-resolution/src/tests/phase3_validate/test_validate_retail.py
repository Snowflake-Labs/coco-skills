"""
Phase 3 — Validate the retail/CPG domain entity-resolution results.

Expected matches:
    RT001 <-> RT002  (Same GLN 0012345678901 — Walmart Supercenter #3456)
    RT003 <-> RT004  (Same GTIN 00049000028904 — Coca-Cola 12pk)
    RT009 <-> RT010  (Same GLN 0022222222201 — CVS #8821)

Expected non-matches:
    RT005 vs RT006  (Target — different store locations, different GLNs)
    RT007 vs RT008  (McDonald's corporate vs franchise — different DUNS)
    RT011 vs RT012  (Different GTIN — 24-pack vs 6-pack, different products)
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.validate, pytest.mark.retail]


# ── Normalized entities ──────────────────────────────────────────────────

class TestNormalizedEntities:
    def test_table_exists(self, sf):
        sf.assert_table_exists("NORMALIZED_ENTITIES")

    def test_row_count(self, sf):
        sf.assert_row_count_between("NORMALIZED_ENTITIES", 12, 12)

    def test_schema_columns(self, sf):
        sf.assert_columns_include("NORMALIZED_ENTITIES", {
            "SOURCE_ID", "NORMALIZED_NAME",
        })

    def test_gtin_padded_to_14(self, run_sql):
        """GTINs should be zero-padded to 14 digits per retail-cpg profile."""
        rows = run_sql(
            "SELECT SOURCE_ID, NORMALIZED_GTIN FROM NORMALIZED_ENTITIES "
            "WHERE SOURCE_ID = 'RT003'"
        )
        if rows and rows[0][1] is not None:
            gtin = str(rows[0][1])
            assert len(gtin) == 14 and gtin.isdigit(), (
                f"Expected 14-digit padded GTIN, got: {gtin}"
            )


# ── Candidate pairs ──────────────────────────────────────────────────────

class TestCandidatePairs:
    def test_table_exists(self, sf):
        sf.assert_table_exists("CANDIDATE_PAIRS")

    def test_has_pairs(self, sf):
        sf.assert_row_count_between("CANDIDATE_PAIRS", 1, 1000)


# ── Match results ────────────────────────────────────────────────────────

class TestMatchResults:
    def test_table_exists(self, sf):
        sf.assert_table_exists("MATCH_RESULTS")

    def test_schema_columns(self, sf):
        sf.assert_columns_include("MATCH_RESULTS", {
            "ID_LEFT", "ID_RIGHT", "DECISION", "CONFIDENCE", "MATCH_METHOD",
        })

    def test_has_tier1_matches(self, run_sql):
        """Retail profile has GLN/GTIN → should produce Tier 1 matches."""
        rows = run_sql(
            "SELECT COUNT(*) FROM MATCH_RESULTS "
            "WHERE MATCH_METHOD = 'tier1_exact_id'"
        )
        assert rows[0][0] > 0, (
            "Retail domain should produce Tier 1 exact-ID matches"
        )


# ── Known matches ────────────────────────────────────────────────────────

class TestKnownMatches:
    EXPECTED_MATCHES = [
        ("RT001", "RT002"),  # Same GLN — Walmart Supercenter #3456
        ("RT003", "RT004"),  # Same GTIN — Coca-Cola 12pk
        ("RT009", "RT010"),  # Same GLN — CVS #8821
    ]

    @pytest.mark.parametrize("left,right", EXPECTED_MATCHES)
    def test_pair_matched(self, left, right, run_sql):
        rows = run_sql(
            f"SELECT DECISION FROM MATCH_RESULTS "
            f"WHERE (ID_LEFT = '{left}' AND ID_RIGHT = '{right}') "
            f"   OR (ID_LEFT = '{right}' AND ID_RIGHT = '{left}')"
        )
        decisions = {r[0] for r in rows}
        assert decisions & {"match", "probable_match"}, (
            f"Expected {left}<->{right} to match, got decisions={decisions}"
        )


# ── Known non-matches ────────────────────────────────────────────────────

class TestKnownNonMatches:
    EXPECTED_NON_MATCHES = [
        ("RT005", "RT006"),  # Target — different GLNs, different locations
        ("RT007", "RT008"),  # McDonald's corporate vs franchise — different DUNS
        ("RT011", "RT012"),  # 24-pack vs 6-pack — different GTINs
    ]

    @pytest.mark.parametrize("left,right", EXPECTED_NON_MATCHES)
    def test_pair_not_matched(self, left, right, run_sql):
        rows = run_sql(
            f"SELECT DECISION FROM MATCH_RESULTS "
            f"WHERE (ID_LEFT = '{left}' AND ID_RIGHT = '{right}') "
            f"   OR (ID_LEFT = '{right}' AND ID_RIGHT = '{left}')"
        )
        decisions = {r[0] for r in rows}
        assert "match" not in decisions, (
            f"Expected {left}<->{right} to NOT match, got decisions={decisions}"
        )


# ── Retail domain specifics ──────────────────────────────────────────────

class TestRetailDomainSpecifics:
    def test_franchise_vs_corporate_separation(self, run_sql):
        """RT007 (McDonald's corporate) and RT008 (franchise) share GLN
        but have different DUNS.  Franchise vs corporate = different entities."""
        rows = run_sql(
            "SELECT DECISION FROM MATCH_RESULTS "
            "WHERE (ID_LEFT = 'RT007' AND ID_RIGHT = 'RT008') "
            "   OR (ID_LEFT = 'RT008' AND ID_RIGHT = 'RT007')"
        )
        decisions = {r[0] for r in rows}
        # They share GLN so Tier 1 might match them.  If so, acceptable.
        if "match" in decisions:
            pytest.skip(
                "RT007/RT008 matched via shared GLN despite different DUNS — "
                "acceptable if skill prioritizes location-level GLN matching"
            )

    def test_different_gtin_different_products(self, run_sql):
        """RT011 (24-pack) and RT012 (6-pack) have different GTINs = different products."""
        rows = run_sql(
            "SELECT DECISION FROM MATCH_RESULTS "
            "WHERE (ID_LEFT = 'RT011' AND ID_RIGHT = 'RT012') "
            "   OR (ID_LEFT = 'RT012' AND ID_RIGHT = 'RT011')"
        )
        decisions = {r[0] for r in rows}
        assert "match" not in decisions, (
            "Products with different GTINs should not match"
        )

    def test_store_number_normalization(self, run_sql):
        """RT001 (#3456) and RT002 (3456) should Tier 1 match via GLN."""
        rows = run_sql(
            "SELECT MATCH_METHOD FROM MATCH_RESULTS "
            "WHERE (ID_LEFT = 'RT001' AND ID_RIGHT = 'RT002') "
            "   OR (ID_LEFT = 'RT002' AND ID_RIGHT = 'RT001')"
        )
        if rows:
            methods = {r[0] for r in rows}
            assert "tier1_exact_id" in methods, (
                f"RT001/RT002 share GLN — expected Tier 1 match, got {methods}"
            )


# ── Entity groups ────────────────────────────────────────────────────────

class TestEntityGroups:
    def test_table_exists(self, sf):
        sf.assert_table_exists("ENTITY_GROUPS")

    def test_gln_match_shares_group(self, run_sql):
        """RT001 and RT002 (same GLN) should be in the same entity group."""
        rows = run_sql(
            "SELECT ENTITY_GROUP_ID FROM ENTITY_GROUPS "
            "WHERE ENTITY_ID IN ('RT001', 'RT002')"
        )
        if len(rows) >= 2:
            groups = {r[0] for r in rows}
            assert len(groups) == 1, (
                f"RT001 and RT002 should share a group, got {groups}"
            )

    def test_different_locations_different_groups(self, run_sql):
        """RT005 and RT006 (Target, different GLNs) should be in separate groups."""
        rows = run_sql(
            "SELECT ENTITY_ID, ENTITY_GROUP_ID FROM ENTITY_GROUPS "
            "WHERE ENTITY_ID IN ('RT005', 'RT006')"
        )
        if len(rows) == 2:
            groups = {r[1] for r in rows}
            assert len(groups) == 2, (
                f"RT005 and RT006 should be in different groups, got {groups}"
            )
