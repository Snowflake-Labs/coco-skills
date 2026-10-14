import importlib.util
from pathlib import Path


def _load_template_conftest():
    template_path = (
        Path(__file__).resolve().parents[1]
        / "dvp-test-setup-generator"
        / "templates"
        / "source"
        / "conftest.py"
    )

    spec = importlib.util.spec_from_file_location("_dvp_source_template_conftest", template_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_ambiguous_column_backticks():
    mod = _load_template_conftest()
    exc = Exception("[AMBIGUOUS_REFERENCE] Reference `return_amount` is ambiguous")
    assert mod._parse_ambiguous_column(exc) == "return_amount"


def test_parse_ambiguous_column_single_quotes():
    mod = _load_template_conftest()
    exc = Exception("Reference 'customer_id' is ambiguous")
    assert mod._parse_ambiguous_column(exc) == "customer_id"


def test_parse_ambiguous_column_no_quotes():
    mod = _load_template_conftest()
    exc = Exception("Reference transaction_date is ambiguous")
    assert mod._parse_ambiguous_column(exc) == "transaction_date"


def test_parse_unresolved_column_with_name_cannot_be_resolved():
    mod = _load_template_conftest()
    exc = Exception(
        "[UNRESOLVED_COLUMN.WITH_SUGGESTION] A column, variable, or function parameter with name `transaction_date` cannot be resolved"
    )
    assert mod._parse_unresolved_column(exc) == "transaction_date"


def test_parse_unresolved_column_cannot_resolve():
    mod = _load_template_conftest()
    exc = Exception("cannot resolve 'foo' given input columns: [bar]")
    assert mod._parse_unresolved_column(exc) == "foo"


def test_suggest_drop_order_falls_back_deterministic():
    mod = _load_template_conftest()
    # No Snowflake connection configured in unit tests -> fallback ordering.
    candidates = ["PRODUCT_CATALOG", "exchange_rates"]
    assert mod._suggest_drop_order("product_id", candidates, asg=None) == [
        "exchange_rates",
        "PRODUCT_CATALOG",
    ]


def test_values_equal_none_and_empty_string():
    root_conftest_path = (
        Path(__file__).resolve().parents[1]
        / "dvp-test-setup-generator"
        / "templates"
        / "conftest.py"
    )
    spec = importlib.util.spec_from_file_location("_dvp_root_template_conftest", root_conftest_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod._values_equal(None, "") is True
    assert mod._values_equal("", None) is True
    assert mod._values_equal(None, None) is True
