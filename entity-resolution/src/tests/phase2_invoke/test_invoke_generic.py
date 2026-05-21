"""Phase 2 — Invoke the entity-resolution skill for the generic domain."""
import pytest

pytestmark = [pytest.mark.invoke, pytest.mark.generic]

PROMPT_TEMPLATE = (
    "I need to run entity resolution on the table {db}.{schema}.GENERIC_SOURCE. "
    "This is a generic name-and-address dataset with no authoritative identifiers. "
    "The columns are: source_id, source_table, raw_name, raw_address, raw_phone, raw_email. "
    "Write all output tables (normalized_entities, candidate_pairs, match_results, entity_groups) "
    "to the schema {db}.{schema}. "
    "Use the entity-resolution skill with the generic domain profile."
)


class TestInvokeGeneric:
    def test_skill_completes(self, invoke_skill, test_schema, sf_connection):
        db = sf_connection.database
        prompt = PROMPT_TEMPLATE.format(db=db, schema=test_schema)
        result = invoke_skill(prompt, timeout=900)
        assert result["ok"], (
            f"Skill invocation failed (rc={result['returncode']}). "
            f"Output tail:\n{result['output'][-3000:]}"
        )
