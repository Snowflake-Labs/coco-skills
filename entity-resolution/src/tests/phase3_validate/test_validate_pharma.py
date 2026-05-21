"""
Phase 3 — Validate the pharma domain entity-resolution results.

Expected matches:
    PH001 <-> PH002  (Same NPI 1234567890 — CVS #4523)
    PH003 <-> PH004  (Same DEA CD9876543 — Springfield Family Pharmacy)
    PH009 <-> PH010  (Same NPI 5555555555 — CVS #12345)

Expected non-matches:
    PH005 vs PH006  (Walgreens — different locations, different NPIs)
    PH007 vs PH008  (Same address block, different suites, different NPIs)
    PH011 vs PH012  (Prescriber vs pharmacy at same address)
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.validate, pytest.mark.pharma]


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

    def test_npi_normalized(self, run_sql):
        """NPIs should be 10-digit zero-padded strings."""
        rows = run_sql(
            "SELECT SOURCE_ID, NORMALIZED_NPI FROM NORMALIZED_ENTITIES "
            "WHERE SOURCE_ID = 'PH001'"
        )
        if rows and rows[0][1] is not None:
            npi = str(rows[0][1])
            assert len(npi) == 10 and npi.isdigit(), (
                f"Expected 10-digit NPI, got: {npi}"
            )


# ── Candidate pairs ──────────────────────────────────────────────────────

class TestCandidatePairs:
    def test_table_exists(self, sf):
        sf.assert_table_exists("CANDIDATE_PAIRS")

    def test_has_pairs(self, sf):
        sf.assert_row_count_between("CANDIDATE_PAIRS", 1, 1000)

    def test_schema_columns(self, sf):
        sf.assert_columns_include("CANDIDATE_PAIRS", {"ID_LEFT", "ID_RIGHT"})


# ── Match results ────────────────────────────────────────────────────────

class TestMatchResults:
    def test_table_exists(self, sf):
        sf.assert_table_exists("MATCH_RESULTS")

    def test_schema_columns(self, sf):
        sf.assert_columns_include("MATCH_RESULTS", {
            "ID_LEFT", "ID_RIGHT", "DECISION", "CONFIDENCE", "MATCH_METHOD",
        })

    def test_has_tier1_matches(self, run_sql):
        """Pharma profile has NPI/DEA/NCPDP → should produce Tier 1 matches."""
        rows = run_sql(
            "SELECT COUNT(*) FROM MATCH_RESULTS "
            "WHERE MATCH_METHOD = 'tier1_exact_id'"
        )
        assert rows[0][0] > 0, "Pharma domain should produce Tier 1 exact-ID matches"

    def test_tier1_confidence_is_1(self, run_sql):
        """Tier 1 deterministic matches should have confidence = 1.0."""
        rows = run_sql(
            "SELECT MIN(CONFIDENCE) FROM MATCH_RESULTS "
            "WHERE MATCH_METHOD = 'tier1_exact_id'"
        )
        if rows and rows[0][0] is not None:
            assert rows[0][0] == 1.0, (
                f"Tier 1 confidence should be 1.0, got min={rows[0][0]}"
            )


# ── Known matches ────────────────────────────────────────────────────────

class TestKnownMatches:
    EXPECTED_MATCHES = [
        ("PH001", "PH002"),  # Same NPI — CVS 4523
        ("PH003", "PH004"),  # Same DEA — Springfield Family Pharmacy
        ("PH009", "PH010"),  # Same NPI — CVS 12345
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
        ("PH005", "PH006"),  # Walgreens — different NPIs, different locations
        ("PH007", "PH008"),  # Same address block, different suites + NPIs
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


# ── Pharma domain specifics ─────────────────────────────────────────────

class TestPharmaDomainSpecifics:
    def test_suite_differentiation(self, run_sql):
        """PH007 (Suite 200) and PH008 (Suite 310) should NOT match."""
        rows = run_sql(
            "SELECT DECISION FROM MATCH_RESULTS "
            "WHERE (ID_LEFT = 'PH007' AND ID_RIGHT = 'PH008') "
            "   OR (ID_LEFT = 'PH008' AND ID_RIGHT = 'PH007')"
        )
        decisions = {r[0] for r in rows}
        assert "match" not in decisions, (
            "Suite-differentiated pharmacies should not match"
        )

    def test_prescriber_vs_pharmacy_isolation(self, run_sql):
        """PH011 (prescriber) and PH012 (pharmacy) should NOT match."""
        rows = run_sql(
            "SELECT DECISION FROM MATCH_RESULTS "
            "WHERE (ID_LEFT = 'PH011' AND ID_RIGHT = 'PH012') "
            "   OR (ID_LEFT = 'PH012' AND ID_RIGHT = 'PH011')"
        )
        decisions = {r[0] for r in rows}
        assert "match" not in decisions, (
            "Prescriber and pharmacy at same address should not match"
        )


# ── Entity groups ────────────────────────────────────────────────────────

class TestEntityGroups:
    def test_table_exists(self, sf):
        sf.assert_table_exists("ENTITY_GROUPS")

    def test_npi_match_shares_group(self, run_sql):
        """PH001 and PH002 (same NPI) should be in the same entity group."""
        rows = run_sql(
            "SELECT ENTITY_GROUP_ID FROM ENTITY_GROUPS "
            "WHERE ENTITY_ID IN ('PH001', 'PH002')"
        )
        if len(rows) >= 2:
            groups = {r[0] for r in rows}
            assert len(groups) == 1, (
                f"PH001 and PH002 should share a group, got {groups}"
            )

    def test_different_npi_different_groups(self, run_sql):
        """PH005 and PH006 (different NPIs, different locations) should be separate."""
        rows = run_sql(
            "SELECT ENTITY_ID, ENTITY_GROUP_ID FROM ENTITY_GROUPS "
            "WHERE ENTITY_ID IN ('PH005', 'PH006')"
        )
        if len(rows) == 2:
            groups = {r[1] for r in rows}
            assert len(groups) == 2, (
                f"PH005 and PH006 should be in different groups, got {groups}"
            )
