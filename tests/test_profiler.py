"""Tests for the profiler module — SQL generation and stats parsing."""

from __future__ import annotations

from dbt_schema_builder.column_types import (
    BooleanColumn,
    FloatColumn,
    IntegerColumn,
    StringColumn,
    UnprofilableColumn,
)
from dbt_schema_builder.profiler import _build_pass_one_sql, _parse_pass_one_row


class TestBuildPassOneSql:
    def test_basic_structure(self):
        columns = [
            StringColumn(column_name="name", data_type="STRING", ordinal_position=1),
            IntegerColumn(column_name="id", data_type="INT64", ordinal_position=2),
        ]
        sql = _build_pass_one_sql(columns, "`proj.ds.tbl`")
        assert "COUNT(*) AS __total_count" in sql
        assert "FROM `proj.ds.tbl`" in sql
        assert "name__null_count" in sql
        assert "id__min_value" in sql

    def test_unprofilable_adds_no_expressions(self):
        columns = [
            UnprofilableColumn(column_name="payload", data_type="JSON", ordinal_position=1),
        ]
        sql = _build_pass_one_sql(columns, "`proj.ds.tbl`")
        assert "payload" not in sql.replace("__total_count", "")

    def test_all_column_types(self):
        columns = [
            StringColumn(column_name="s", data_type="STRING", ordinal_position=1),
            IntegerColumn(column_name="i", data_type="INT64", ordinal_position=2),
            FloatColumn(column_name="f", data_type="FLOAT64", ordinal_position=3),
            BooleanColumn(column_name="b", data_type="BOOL", ordinal_position=4),
        ]
        sql = _build_pass_one_sql(columns, "`proj.ds.tbl`")
        assert "s__max_length" in sql
        assert "i__min_value" in sql
        assert "f__min_value" in sql
        assert "b__true_count" in sql


class TestParsePassOneRow:
    def test_basic_parsing(self):
        columns = [
            StringColumn(column_name="name", data_type="STRING", ordinal_position=1),
            IntegerColumn(column_name="age", data_type="INT64", ordinal_position=2),
        ]
        row = {
            "__total_count": 1000,
            "name__null_count": 5,
            "name__distinct_count": 200,
            "name__max_length": 50,
            "age__null_count": 0,
            "age__distinct_count": 80,
            "age__min_value": 18,
            "age__max_value": 99,
        }
        stats = _parse_pass_one_row(row, columns)

        assert stats["name"].null_count == 5
        assert stats["name"].distinct_count == 200
        assert stats["name"].total_count == 1000
        assert stats["name"].extra["max_length"] == 50

        assert stats["age"].null_count == 0
        assert stats["age"].distinct_count == 80
        assert stats["age"].extra["min_value"] == 18
        assert stats["age"].extra["max_value"] == 99

    def test_unprofilable_column_has_defaults(self):
        columns = [
            UnprofilableColumn(column_name="payload", data_type="JSON", ordinal_position=1),
        ]
        row = {"__total_count": 100}
        stats = _parse_pass_one_row(row, columns)
        assert stats["payload"].null_count == 0
        assert stats["payload"].distinct_count == 0
        assert stats["payload"].total_count == 100
