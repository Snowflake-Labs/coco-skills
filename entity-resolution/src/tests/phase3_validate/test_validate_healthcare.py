"""
Phase 3 — Validate the healthcare provider domain entity-resolution results.

Expected matches:
    HC001 <-> HC002  (Same NPI 1234567890, Type 1 — Dr. John Smith)
    HC003 <-> HC004  (Same NPI 9876543210, Type 2 — Springfield Medical Group)
    HC007 <-> HC008  (Same NPI 3333333333, Type 1, different cities — multi-location)
    HC011 <-> HC012  (Same NPI 6666666666, Type 1 — Maria Garcia)

Expected non-matches:
    HC005 vs HC006  (Type 1 individual vs Type 2 org — MUST NOT cross-match)
    HC009 vs HC010  (Same building, different suites, different NPIs)
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.validate, pytest.mark.healthcare]


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
            "WHERE SOURCE_ID = 'HC001'"
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


# ── Match results ────────────────────────────────────────────────────────

class TestMatchResults:
    def test_table_exists(self, sf):
        sf.assert_table_exists("MATCH_RESULTS")

    def test_schema_columns(self, sf):
        sf.assert_columns_include("MATCH_RESULTS", {
            "ID_LEFT", "ID_RIGHT", "DECISION", "CONFIDENCE", "MATCH_METHOD",
        })

    def test_has_tier1_matches(self, run_sql):
        """Healthcare profile has NPI → should produce Tier 1 matches."""
        rows = run_sql(
            "SELECT COUNT(*) FROM MATCH_RESULTS "
            "WHERE MATCH_METHOD = 'tier1_exact_id'"
        )
        assert rows[0][0] > 0, (
            "Healthcare domain should produce Tier 1 NPI matches"
        )


# ── Known matches ────────────────────────────────────────────────────────

class TestKnownMatches:
    EXPECTED_MATCHES = [
        ("HC001", "HC002"),  # Same NPI, Type 1 — Dr. John Smith
        ("HC003", "HC004"),  # Same NPI, Type 2 — Springfield Medical Group
        ("HC007", "HC008"),  # Same NPI, Type 1, different cities — multi-location
        ("HC011", "HC012"),  # Same NPI, Type 1 — Maria Garcia
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
        ("HC005", "HC006"),  # Type 1 individual vs Type 2 org
        ("HC009", "HC010"),  # Same building, different suites, different NPIs
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


# ── Healthcare domain specifics ──────────────────────────────────────────

class TestHealthcareDomainSpecifics:
    def test_npi_type_isolation(self, run_sql):
        """HC005 (NPI Type 1) and HC006 (NPI Type 2) MUST NOT cross-match."""
        rows = run_sql(
            "SELECT DECISION FROM MATCH_RESULTS "
            "WHERE (ID_LEFT = 'HC005' AND ID_RIGHT = 'HC006') "
            "   OR (ID_LEFT = 'HC006' AND ID_RIGHT = 'HC005')"
        )
        decisions = {r[0] for r in rows}
        assert "match" not in decisions, (
            "NPI Type 1 (individual) and Type 2 (org) must not cross-match"
        )

    def test_multi_location_same_npi(self, run_sql):
        """HC007 and HC008 share NPI but are in different cities — same entity."""
        rows = run_sql(
            "SELECT DECISION FROM MATCH_RESULTS "
            "WHERE (ID_LEFT = 'HC007' AND ID_RIGHT = 'HC008') "
            "   OR (ID_LEFT = 'HC008' AND ID_RIGHT = 'HC007')"
        )
        decisions = {r[0] for r in rows}
        assert decisions & {"match", "probable_match"}, (
            "Multi-location providers with same NPI should match"
        )

    def test_suite_differentiation(self, run_sql):
        """HC009 (Suite 210) and HC010 (Suite 220) — different NPIs, different suites."""
        rows = run_sql(
            "SELECT DECISION FROM MATCH_RESULTS "
            "WHERE (ID_LEFT = 'HC009' AND ID_RIGHT = 'HC010') "
            "   OR (ID_LEFT = 'HC010' AND ID_RIGHT = 'HC009')"
        )
        decisions = {r[0] for r in rows}
        assert "match" not in decisions, (
            "Different suites with different NPIs should not match"
        )


# ── Entity groups ────────────────────────────────────────────────────────

class TestEntityGroups:
    def test_table_exists(self, sf):
        sf.assert_table_exists("ENTITY_GROUPS")

    def test_npi_match_shares_group(self, run_sql):
        """HC001 and HC002 (same NPI) should be in the same entity group."""
        rows = run_sql(
            "SELECT ENTITY_GROUP_ID FROM ENTITY_GROUPS "
            "WHERE ENTITY_ID IN ('HC001', 'HC002')"
        )
        if len(rows) >= 2:
            groups = {r[0] for r in rows}
            assert len(groups) == 1, (
                f"HC001 and HC002 should share a group, got {groups}"
            )

    def test_type_isolation_different_groups(self, run_sql):
        """HC005 (Type 1) and HC006 (Type 2) should be in different groups."""
        rows = run_sql(
            "SELECT ENTITY_ID, ENTITY_GROUP_ID FROM ENTITY_GROUPS "
            "WHERE ENTITY_ID IN ('HC005', 'HC006')"
        )
        if len(rows) == 2:
            groups = {r[1] for r in rows}
            assert len(groups) == 2, (
                f"HC005 and HC006 should be in different groups, got {groups}"
            )
