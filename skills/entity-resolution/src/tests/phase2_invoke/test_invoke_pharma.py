"""Phase 2 — Invoke the entity-resolution skill for the pharma domain."""
import pytest

pytestmark = [pytest.mark.invoke, pytest.mark.pharma]

PROMPT_TEMPLATE = (
    "I need to run entity resolution on the table {db}.{schema}.PHARMA_SOURCE. "
    "This is a pharma dataset with pharmacy/prescriber records. "
    "The columns are: source_id, source_table, raw_name, raw_address, raw_npi, raw_dea, raw_ncpdp. "
    "Write all output tables (normalized_entities, candidate_pairs, match_results, entity_groups) "
    "to the schema {db}.{schema}. "
    "Use the entity-resolution skill with the pharma domain profile."
)


class TestInvokePharma:
    def test_skill_completes(self, invoke_skill, test_schema, sf_connection):
        db = sf_connection.database
        prompt = PROMPT_TEMPLATE.format(db=db, schema=test_schema)
        result = invoke_skill(prompt, timeout=900)
        assert result["ok"], (
            f"Skill invocation failed (rc={result['returncode']}). "
            f"Output tail:\n{result['output'][-3000:]}"
        )
