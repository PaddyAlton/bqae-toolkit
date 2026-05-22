"""Tests for the column_types discriminated union and per-type logic."""

from __future__ import annotations

from pydantic import TypeAdapter

from dbt_schema_builder.column_types import (
    BooleanColumn,
    FallbackColumn,
    FloatColumn,
    IntegerColumn,
    PassOneStats,
    ProfiledColumn,
    StringColumn,
    TimestampColumn,
    UnprofilableColumn,
    column_type_discriminator,
)

adapter = TypeAdapter(ProfiledColumn)


# ---------------------------------------------------------------------------
# Discriminator mapping
# ---------------------------------------------------------------------------


class TestDiscriminator:
    def test_string(self):
        assert column_type_discriminator({"data_type": "STRING"}) == "string"

    def test_integer_types(self):
        assert column_type_discriminator({"data_type": "INT64"}) == "integer"
        assert column_type_discriminator({"data_type": "INTEGER"}) == "integer"

    def test_float_types(self):
        assert column_type_discriminator({"data_type": "FLOAT64"}) == "float"
        assert column_type_discriminator({"data_type": "NUMERIC"}) == "float"
        assert column_type_discriminator({"data_type": "BIGNUMERIC"}) == "float"

    def test_boolean_types(self):
        assert column_type_discriminator({"data_type": "BOOL"}) == "boolean"
        assert column_type_discriminator({"data_type": "BOOLEAN"}) == "boolean"

    def test_timestamp_types(self):
        assert column_type_discriminator({"data_type": "TIMESTAMP"}) == "timestamp"
        assert column_type_discriminator({"data_type": "DATETIME"}) == "timestamp"
        assert column_type_discriminator({"data_type": "DATE"}) == "timestamp"

    def test_unprofilable(self):
        assert column_type_discriminator({"data_type": "JSON"}) == "unprofilable"
        assert column_type_discriminator({"data_type": "GEOGRAPHY"}) == "unprofilable"
        assert column_type_discriminator({"data_type": "ARRAY<STRING>"}) == "unprofilable"
        assert column_type_discriminator({"data_type": "STRUCT<a INT64>"}) == "unprofilable"

    def test_fallback(self):
        assert column_type_discriminator({"data_type": "BYTES"}) == "fallback"
        assert column_type_discriminator({"data_type": ""}) == "fallback"

    def test_case_insensitive(self):
        assert column_type_discriminator({"data_type": "string"}) == "string"
        assert column_type_discriminator({"data_type": "int64"}) == "integer"


# ---------------------------------------------------------------------------
# Parsing via Pydantic discriminated union
# ---------------------------------------------------------------------------


class TestParsing:
    def test_string_column(self):
        col = adapter.validate_python({"column_name": "name", "data_type": "STRING", "ordinal_position": 1})
        assert isinstance(col, StringColumn)

    def test_integer_column(self):
        col = adapter.validate_python({"column_name": "id", "data_type": "INT64", "ordinal_position": 1})
        assert isinstance(col, IntegerColumn)

    def test_float_column(self):
        col = adapter.validate_python({"column_name": "price", "data_type": "FLOAT64", "ordinal_position": 1})
        assert isinstance(col, FloatColumn)

    def test_boolean_column(self):
        col = adapter.validate_python({"column_name": "active", "data_type": "BOOL", "ordinal_position": 1})
        assert isinstance(col, BooleanColumn)

    def test_timestamp_column(self):
        col = adapter.validate_python({"column_name": "created_at", "data_type": "TIMESTAMP", "ordinal_position": 1})
        assert isinstance(col, TimestampColumn)

    def test_unprofilable_column(self):
        col = adapter.validate_python({"column_name": "payload", "data_type": "JSON", "ordinal_position": 1})
        assert isinstance(col, UnprofilableColumn)

    def test_fallback_column(self):
        col = adapter.validate_python({"column_name": "raw", "data_type": "BYTES", "ordinal_position": 1})
        assert isinstance(col, FallbackColumn)


# ---------------------------------------------------------------------------
# Pass-one expressions
# ---------------------------------------------------------------------------


class TestPassOneExpressions:
    def test_string_has_max_length(self):
        col = StringColumn(column_name="name", data_type="STRING", ordinal_position=1)
        exprs = col.pass_one_expressions()
        joined = " ".join(exprs)
        assert "MAX(LENGTH(name))" in joined
        assert "name__max_length" in joined

    def test_integer_has_min_max(self):
        col = IntegerColumn(column_name="id", data_type="INT64", ordinal_position=1)
        exprs = col.pass_one_expressions()
        joined = " ".join(exprs)
        assert "MIN(id)" in joined
        assert "MAX(id)" in joined

    def test_boolean_has_true_count(self):
        col = BooleanColumn(column_name="flag", data_type="BOOL", ordinal_position=1)
        exprs = col.pass_one_expressions()
        joined = " ".join(exprs)
        assert "COUNTIF(flag IS TRUE)" in joined

    def test_unprofilable_has_no_expressions(self):
        col = UnprofilableColumn(column_name="payload", data_type="JSON", ordinal_position=1)
        assert col.pass_one_expressions() == []


# ---------------------------------------------------------------------------
# Pass-two triggers
# ---------------------------------------------------------------------------


class TestPassTwo:
    def test_string_needs_pass_two_low_cardinality(self):
        col = StringColumn(column_name="status", data_type="STRING", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=5, total_count=100)
        assert col.needs_pass_two(stats) is True

    def test_string_no_pass_two_high_cardinality(self):
        col = StringColumn(column_name="name", data_type="STRING", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=50, total_count=100)
        assert col.needs_pass_two(stats) is False

    def test_string_no_pass_two_zero_distinct(self):
        col = StringColumn(column_name="empty", data_type="STRING", ordinal_position=1)
        stats = PassOneStats(null_count=100, distinct_count=0, total_count=100)
        assert col.needs_pass_two(stats) is False

    def test_integer_needs_pass_two_low_cardinality(self):
        col = IntegerColumn(column_name="category_id", data_type="INT64", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=3, total_count=100)
        assert col.needs_pass_two(stats) is True

    def test_float_never_needs_pass_two(self):
        col = FloatColumn(column_name="price", data_type="FLOAT64", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=5, total_count=100)
        assert col.needs_pass_two(stats) is False

    def test_pass_two_query_string(self):
        col = StringColumn(column_name="status", data_type="STRING", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=3, total_count=100)
        sql = col.pass_two_query(stats, "`project.dataset.table`")
        assert sql is not None
        assert "SELECT DISTINCT status" in sql
        assert "`project.dataset.table`" in sql


# ---------------------------------------------------------------------------
# Test generation
# ---------------------------------------------------------------------------


class TestDbtTests:
    def test_string_not_null_unique(self):
        col = StringColumn(column_name="id", data_type="STRING", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=100, total_count=100)
        tests = col.to_dbt_tests(stats)
        assert "not_null" in tests
        assert "unique" in tests

    def test_string_with_accepted_values(self):
        col = StringColumn(column_name="status", data_type="STRING", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=3, total_count=100)
        tests = col.to_dbt_tests(stats, pass_two_result=["active", "inactive", "pending"])
        av_test = [t for t in tests if isinstance(t, dict) and "accepted_values" in t]
        assert len(av_test) == 1
        assert av_test[0]["accepted_values"]["arguments"]["values"] == ["active", "inactive", "pending"]
        assert av_test[0]["accepted_values"]["config"]["severity"] == "warn"

    def test_integer_accepted_values_quote_false(self):
        col = IntegerColumn(column_name="cat_id", data_type="INT64", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=3, total_count=100, extra={"min_value": 1, "max_value": 3})
        tests = col.to_dbt_tests(stats, pass_two_result=[1, 2, 3])
        av_test = [t for t in tests if isinstance(t, dict) and "accepted_values" in t]
        assert len(av_test) == 1
        assert av_test[0]["accepted_values"]["arguments"]["quote"] is False
        assert av_test[0]["accepted_values"]["config"]["severity"] == "warn"

    def test_integer_accepted_range(self):
        col = IntegerColumn(column_name="score", data_type="INT64", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=50, total_count=100, extra={"min_value": 0, "max_value": 100})
        tests = col.to_dbt_tests(stats)
        range_test = [t for t in tests if isinstance(t, dict) and "dbt_utils.accepted_range" in t]
        assert len(range_test) == 1
        assert range_test[0]["dbt_utils.accepted_range"]["arguments"] == {"min_value": 0, "max_value": 100}
        assert range_test[0]["dbt_utils.accepted_range"]["config"]["severity"] == "warn"

    def test_float_accepted_range(self):
        col = FloatColumn(column_name="price", data_type="FLOAT64", ordinal_position=1)
        stats = PassOneStats(
            null_count=0, distinct_count=50, total_count=100, extra={"min_value": 0.5, "max_value": 99.9}
        )
        tests = col.to_dbt_tests(stats)
        assert "not_null" in tests
        range_test = [t for t in tests if isinstance(t, dict) and "dbt_utils.accepted_range" in t]
        assert len(range_test) == 1

    def test_boolean_not_null_only(self):
        col = BooleanColumn(column_name="active", data_type="BOOL", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=2, total_count=100, extra={"true_count": 80})
        tests = col.to_dbt_tests(stats)
        assert tests == ["not_null"]

    def test_timestamp_accepted_range(self):
        col = TimestampColumn(column_name="created_at", data_type="TIMESTAMP", ordinal_position=1)
        stats = PassOneStats(
            null_count=0,
            distinct_count=100,
            total_count=100,
            extra={"min_value": "2020-01-01T00:00:00", "max_value": "2024-12-31T23:59:59"},
        )
        tests = col.to_dbt_tests(stats)
        assert "not_null" in tests
        range_test = [t for t in tests if isinstance(t, dict) and "dbt_utils.accepted_range" in t]
        assert len(range_test) == 1

    def test_all_null_no_tests(self):
        col = StringColumn(column_name="empty", data_type="STRING", ordinal_position=1)
        stats = PassOneStats(null_count=100, distinct_count=0, total_count=100)
        assert col.to_dbt_tests(stats) == []

    def test_all_null_meta(self):
        col = StringColumn(column_name="empty", data_type="STRING", ordinal_position=1)
        stats = PassOneStats(null_count=100, distinct_count=0, total_count=100)
        assert col.to_dbt_meta(stats) == {"all_null_warning": True}

    def test_meta_empty_when_no_warning(self):
        col = IntegerColumn(column_name="id", data_type="INT64", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=100, total_count=100)
        assert col.to_dbt_meta(stats) == {}

    def test_unprofilable_no_tests(self):
        col = UnprofilableColumn(column_name="payload", data_type="JSON", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=0, total_count=100)
        assert col.to_dbt_tests(stats) == []
        assert col.to_dbt_meta(stats) == {}

    def test_fallback_not_null(self):
        col = FallbackColumn(column_name="raw", data_type="BYTES", ordinal_position=1)
        stats = PassOneStats(null_count=0, distinct_count=50, total_count=100)
        tests = col.to_dbt_tests(stats)
        assert "not_null" in tests
