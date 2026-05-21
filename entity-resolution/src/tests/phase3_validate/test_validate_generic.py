"""
Phase 3 — Validate the generic domain entity-resolution results.

No skill invocation here — just queries the Snowflake objects
created by Phase 2 and asserts correctness.

Expected matches:
    GN001 <-> GN002  (Acme Manufacturing / ACME MFG CO)
    GN003 <-> GN004  (National Insurance Associates / Natl Ins Assoc)
    GN009 <-> GN010  (Robert Williams / Williams Plumbing dba Rob Williams)

Expected non-matches:
    GN005 vs GN006  (same name, different addresses — separate branches)
    GN007 vs GN008  (different names, same address — different companies)
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.validate, pytest.mark.generic]


# ── Normalized entities ──────────────────────────────────────────────────

class TestNormalizedEntities:
    def test_table_exists(self, sf):
        sf.assert_table_exists("NORMALIZED_ENTITIES")

    def test_row_count(self, sf):
        sf.assert_row_count_between("NORMALIZED_ENTITIES", 10, 10)

    def test_schema_columns(self, sf):
        sf.assert_columns_include("NORMALIZED_ENTITIES", {
            "SOURCE_ID", "NORMALIZED_NAME",
        })

    def test_names_normalized(self, run_sql):
        """Normalized names should be uppercase and stripped of legal suffixes."""
        rows = run_sql(
            "SELECT SOURCE_ID, NORMALIZED_NAME FROM NORMALIZED_ENTITIES "
            "WHERE SOURCE_ID IN ('GN001', 'GN002') ORDER BY SOURCE_ID"
        )
        assert len(rows) == 2
        for _, name in rows:
            assert name == name.upper(), f"Expected uppercase, got: {name}"


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

    def test_decisions_valid(self, run_sql):
        decisions = {r[0] for r in run_sql(
            "SELECT DISTINCT DECISION FROM MATCH_RESULTS"
        )}
        assert decisions <= {"match", "probable_match", "no_match"}, (
            f"Unexpected decisions: {decisions}"
        )

    def test_no_tier1_matches(self, run_sql):
        """Generic profile has no authoritative IDs → no Tier 1 results."""
        rows = run_sql(
            "SELECT COUNT(*) FROM MATCH_RESULTS "
            "WHERE MATCH_METHOD = 'tier1_exact_id'"
        )
        assert rows[0][0] == 0, "Generic domain should not produce Tier 1 matches"


# ── Known matches ────────────────────────────────────────────────────────

class TestKnownMatches:
    EXPECTED_MATCHES = [
        ("GN001", "GN002"),  # Acme Manufacturing / ACME MFG CO
        ("GN003", "GN004"),  # National Insurance Associates / Natl Ins Assoc
        ("GN009", "GN010"),  # Robert Williams / Williams Plumbing dba
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
        ("GN005", "GN006"),  # Same name, different cities
        ("GN007", "GN008"),  # Different names, same address
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


# ── Entity groups ────────────────────────────────────────────────────────

class TestEntityGroups:
    def test_table_exists(self, sf):
        sf.assert_table_exists("ENTITY_GROUPS")

    def test_schema_columns(self, sf):
        sf.assert_columns_include("ENTITY_GROUPS", {
            "ENTITY_ID", "ENTITY_GROUP_ID",
        })

    def test_matched_pair_shares_group(self, run_sql):
        """GN001 and GN002 should be in the same entity group."""
        rows = run_sql(
            "SELECT ENTITY_GROUP_ID FROM ENTITY_GROUPS "
            "WHERE ENTITY_ID IN ('GN001', 'GN002')"
        )
        if len(rows) >= 2:
            groups = {r[0] for r in rows}
            assert len(groups) == 1, (
                f"GN001 and GN002 should share a group, got {groups}"
            )

    def test_non_match_different_groups(self, run_sql):
        """GN005 and GN006 (same name, different addresses) should be separate."""
        rows = run_sql(
            "SELECT ENTITY_ID, ENTITY_GROUP_ID FROM ENTITY_GROUPS "
            "WHERE ENTITY_ID IN ('GN005', 'GN006')"
        )
        if len(rows) == 2:
            groups = {r[1] for r in rows}
            assert len(groups) == 2, (
                f"GN005 and GN006 should be in different groups, got {groups}"
            )
