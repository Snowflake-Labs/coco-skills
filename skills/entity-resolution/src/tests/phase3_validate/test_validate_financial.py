"""
Phase 3 — Validate the financial services domain entity-resolution results.

Expected matches:
    FN001 <-> FN002  (Same LEI 784F5XWPLTWKTBV3E584 — Goldman Sachs)
    FN003 <-> FN004  (Same DUNS 987654321 — Midwest Financial Advisors)
    FN007 <-> FN008  (Same LEI 7LTWFZYICNSX8D621K86 — Deutsche Bank)

Expected non-matches:
    FN005 vs FN006  (JPMorgan parent vs subsidiary — different LEIs)
    FN009 vs FN010  (HSBC Holdings PLC vs HSBC Bank USA — different LEIs)
    FN011 vs FN012  (Different firms at same address — different DUNS/Tax IDs)
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.validate, pytest.mark.financial]


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

    def test_lei_normalized(self, run_sql):
        """LEIs should be 20-char uppercase alphanumeric."""
        rows = run_sql(
            "SELECT SOURCE_ID, NORMALIZED_LEI FROM NORMALIZED_ENTITIES "
            "WHERE SOURCE_ID = 'FN001'"
        )
        if rows and rows[0][1] is not None:
            lei = str(rows[0][1])
            assert len(lei) == 20 and lei.isalnum(), (
                f"Expected 20-char alphanumeric LEI, got: {lei}"
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
        """Financial profile has LEI/DUNS/Tax ID → should produce Tier 1 matches."""
        rows = run_sql(
            "SELECT COUNT(*) FROM MATCH_RESULTS "
            "WHERE MATCH_METHOD = 'tier1_exact_id'"
        )
        assert rows[0][0] > 0, (
            "Financial domain should produce Tier 1 exact-ID matches"
        )


# ── Known matches ────────────────────────────────────────────────────────

class TestKnownMatches:
    EXPECTED_MATCHES = [
        ("FN001", "FN002"),  # Same LEI — Goldman Sachs
        ("FN003", "FN004"),  # Same DUNS — Midwest Financial Advisors
        ("FN007", "FN008"),  # Same LEI — Deutsche Bank
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
        ("FN005", "FN006"),  # JPMorgan parent vs subsidiary — different LEIs
        ("FN009", "FN010"),  # HSBC Holdings vs HSBC Bank USA — different LEIs
        ("FN011", "FN012"),  # Different firms, same address, different DUNS
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


# ── Financial domain specifics ───────────────────────────────────────────

class TestFinancialDomainSpecifics:
    def test_parent_subsidiary_separation(self, run_sql):
        """FN005 (JPMorgan Chase & Co) and FN006 (JPMorgan Chase Bank NA)
        have different LEIs — parent vs subsidiary = separate."""
        rows = run_sql(
            "SELECT DECISION FROM MATCH_RESULTS "
            "WHERE (ID_LEFT = 'FN005' AND ID_RIGHT = 'FN006') "
            "   OR (ID_LEFT = 'FN006' AND ID_RIGHT = 'FN005')"
        )
        decisions = {r[0] for r in rows}
        assert "match" not in decisions, (
            "Parent and subsidiary with different LEIs should not match"
        )

    def test_cross_jurisdiction_separation(self, run_sql):
        """FN009 (HSBC Holdings PLC, UK) and FN010 (HSBC Bank USA)
        have different LEIs — different jurisdictions = separate."""
        rows = run_sql(
            "SELECT DECISION FROM MATCH_RESULTS "
            "WHERE (ID_LEFT = 'FN009' AND ID_RIGHT = 'FN010') "
            "   OR (ID_LEFT = 'FN010' AND ID_RIGHT = 'FN009')"
        )
        decisions = {r[0] for r in rows}
        assert "match" not in decisions, (
            "Cross-jurisdiction entities with different LEIs should not match"
        )


# ── Entity groups ────────────────────────────────────────────────────────

class TestEntityGroups:
    def test_table_exists(self, sf):
        sf.assert_table_exists("ENTITY_GROUPS")

    def test_lei_match_shares_group(self, run_sql):
        """FN001 and FN002 (same LEI) should be in the same entity group."""
        rows = run_sql(
            "SELECT ENTITY_GROUP_ID FROM ENTITY_GROUPS "
            "WHERE ENTITY_ID IN ('FN001', 'FN002')"
        )
        if len(rows) >= 2:
            groups = {r[0] for r in rows}
            assert len(groups) == 1, (
                f"FN001 and FN002 should share a group, got {groups}"
            )

    def test_parent_subsidiary_different_groups(self, run_sql):
        """FN005 and FN006 (parent vs subsidiary) should be in separate groups."""
        rows = run_sql(
            "SELECT ENTITY_ID, ENTITY_GROUP_ID FROM ENTITY_GROUPS "
            "WHERE ENTITY_ID IN ('FN005', 'FN006')"
        )
        if len(rows) == 2:
            groups = {r[1] for r in rows}
            assert len(groups) == 2, (
                f"FN005 and FN006 should be in different groups, got {groups}"
            )
