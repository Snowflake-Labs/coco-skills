"""Phase 2 — Invoke the entity-resolution skill for the retail/CPG domain."""
import pytest

pytestmark = [pytest.mark.invoke, pytest.mark.retail]

PROMPT_TEMPLATE = (
    "I need to run entity resolution on the table {db}.{schema}.RETAIL_SOURCE. "
    "This is a retail/CPG dataset with store locations and product records. "
    "The columns are: source_id, source_table, raw_name, raw_address, raw_gtin, "
    "raw_gln, raw_supplier_id, raw_duns, entity_type. "
    "Write all output tables (normalized_entities, candidate_pairs, match_results, entity_groups) "
    "to the schema {db}.{schema}. "
    "Use the entity-resolution skill with the retail-cpg domain profile."
)


class TestInvokeRetail:
    def test_skill_completes(self, invoke_skill, test_schema, sf_connection):
        db = sf_connection.database
        prompt = PROMPT_TEMPLATE.format(db=db, schema=test_schema)
        result = invoke_skill(prompt, timeout=900)
        assert result["ok"], (
            f"Skill invocation failed (rc={result['returncode']}). "
            f"Output tail:\n{result['output'][-3000:]}"
        )
