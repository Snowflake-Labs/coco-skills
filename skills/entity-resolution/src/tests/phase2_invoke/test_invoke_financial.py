"""Phase 2 — Invoke the entity-resolution skill for the financial services domain."""
import pytest

pytestmark = [pytest.mark.invoke, pytest.mark.financial]

PROMPT_TEMPLATE = (
    "I need to run entity resolution on the table {db}.{schema}.FINANCIAL_SOURCE. "
    "This is a financial services dataset with bank and investment firm records. "
    "The columns are: source_id, source_table, raw_name, raw_address, raw_lei, "
    "raw_duns, raw_tax_id, raw_crd, raw_swift. "
    "Write all output tables (normalized_entities, candidate_pairs, match_results, entity_groups) "
    "to the schema {db}.{schema}. "
    "Use the entity-resolution skill with the financial-services domain profile."
)


class TestInvokeFinancial:
    def test_skill_completes(self, invoke_skill, test_schema, sf_connection):
        db = sf_connection.database
        prompt = PROMPT_TEMPLATE.format(db=db, schema=test_schema)
        result = invoke_skill(prompt, timeout=900)
        assert result["ok"], (
            f"Skill invocation failed (rc={result['returncode']}). "
            f"Output tail:\n{result['output'][-3000:]}"
        )
