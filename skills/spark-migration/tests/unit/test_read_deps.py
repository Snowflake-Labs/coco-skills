"""Tests for dependency read tools."""

import sma_api


# ---------------------------------------------------------------------------
# get_dependency_summary
# ---------------------------------------------------------------------------

class TestGetDependencySummary:
    def test_returns_summary(self, seeded_db_with_deps):
        result = sma_api.get_dependency_summary(seeded_db_with_deps)
        assert result["total_islands"] >= 1
        assert result["total_files_with_dependencies"] > 0
        assert isinstance(result["islands"], list)

    def test_no_dependency_tables(self, seeded_db):
        """Returns error when no dependency tables exist."""
        result = sma_api.get_dependency_summary(seeded_db)
        assert "error" in result


# ---------------------------------------------------------------------------
# get_file_dependencies
# ---------------------------------------------------------------------------

class TestGetFileDependencies:
    def test_returns_deps_for_file(self, seeded_db_with_deps):
        result = sma_api.get_file_dependencies(
            seeded_db_with_deps, "src/etl/pipeline.py"
        )
        assert isinstance(result, list)
        assert len(result) >= 1
        dep_names = {r["dependency"] for r in result}
        assert "src/etl/loader.py" in dep_names

    def test_file_not_found_returns_empty(self, seeded_db_with_deps):
        result = sma_api.get_file_dependencies(
            seeded_db_with_deps, "nonexistent.py"
        )
        assert result == []

    def test_no_dependency_tables_returns_error(self, seeded_db):
        result = sma_api.get_file_dependencies(seeded_db, "any.py")
        assert len(result) == 1
        assert "error" in result[0]


# ---------------------------------------------------------------------------
# get_dependency_inventory
# ---------------------------------------------------------------------------

class TestGetDependencyInventory:
    def test_returns_all_rows(self, seeded_db_with_deps):
        result = sma_api.get_dependency_inventory(seeded_db_with_deps)
        assert isinstance(result, list)
        assert len(result) == 4  # 4 rows in DEPENDENCY_CSV

    def test_no_dependency_tables_returns_error(self, seeded_db):
        result = sma_api.get_dependency_inventory(seeded_db)
        assert len(result) == 1
        assert "error" in result[0]


# ---------------------------------------------------------------------------
# get_dependency_graph
# ---------------------------------------------------------------------------

class TestGetDependencyGraph:
    def test_returns_graph_data(self, seeded_db_with_deps):
        result = sma_api.get_dependency_graph(seeded_db_with_deps)
        assert "nodes" in result
        assert "edges" in result
        assert "islands" in result
        # There should be at least 1 edge (pipeline -> loader)
        assert len(result["edges"]) >= 1

    def test_edge_has_source_and_target(self, seeded_db_with_deps):
        result = sma_api.get_dependency_graph(seeded_db_with_deps)
        edge = result["edges"][0]
        assert "source" in edge
        assert "target" in edge
        assert "island" in edge
