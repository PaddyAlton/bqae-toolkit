"""Tests for output schema assembly and YAML serialisation."""

from __future__ import annotations

from dbt_schema_builder.column_types import (
    IntegerColumn,
    PassOneStats,
    StringColumn,
    UnprofilableColumn,
)
from dbt_schema_builder.output import DbtColumnSchema, DbtModelSchema, assemble_schema, render_yaml


class TestAssembleSchema:
    def test_basic_assembly(self):
        columns = [
            StringColumn(column_name="name", data_type="STRING", ordinal_position=1),
            IntegerColumn(column_name="id", data_type="INT64", ordinal_position=2),
        ]
        pass_one_stats = {
            "name": PassOneStats(null_count=0, distinct_count=100, total_count=100, extra={"max_length": 30}),
            "id": PassOneStats(
                null_count=0, distinct_count=100, total_count=100, extra={"min_value": 1, "max_value": 100}
            ),
        }
        schema = assemble_schema(
            model_name="my_model",
            table_description="A test model",
            columns=columns,
            pass_one_stats=pass_one_stats,
            pass_two_results={},
            column_descriptions={"name": "The name", "id": "The ID"},
        )
        assert schema.name == "my_model"
        assert schema.description == "A test model"
        assert len(schema.columns) == 2
        assert schema.columns[0].name == "name"
        assert schema.columns[0].description == "The name"
        assert "not_null" in schema.columns[0].data_tests
        assert "unique" in schema.columns[0].data_tests

    def test_unprofilable_column_in_assembly(self):
        columns = [
            UnprofilableColumn(column_name="payload", data_type="JSON", ordinal_position=1),
        ]
        schema = assemble_schema(
            model_name="test",
            table_description="",
            columns=columns,
            pass_one_stats={},
            pass_two_results={},
            column_descriptions={"payload": "JSON data"},
        )
        assert schema.columns[0].data_tests == []
        assert schema.columns[0].data_type == "JSON"
        assert schema.columns[0].meta == {}

    def test_all_null_column(self):
        columns = [
            StringColumn(column_name="empty", data_type="STRING", ordinal_position=1),
        ]
        pass_one_stats = {
            "empty": PassOneStats(null_count=100, distinct_count=0, total_count=100),
        }
        schema = assemble_schema(
            model_name="test",
            table_description="",
            columns=columns,
            pass_one_stats=pass_one_stats,
            pass_two_results={},
            column_descriptions={},
        )
        assert schema.columns[0].data_tests == []
        assert schema.columns[0].data_type == "STRING"
        assert schema.columns[0].meta == {"all_null_warning": True}


class TestRenderYaml:
    def test_basic_yaml_structure(self):
        schema = DbtModelSchema(
            name="my_model",
            description="A model",
            columns=[
                DbtColumnSchema(name="id", description="Primary key", data_tests=["not_null", "unique"]),
                DbtColumnSchema(name="name", description="The name"),
            ],
        )
        yaml_str = render_yaml(schema)
        assert "models:" in yaml_str
        assert "name: my_model" in yaml_str
        assert "description: |" in yaml_str
        assert "A model" in yaml_str
        assert "- not_null" in yaml_str
        assert "- unique" in yaml_str

    def test_accepted_values_in_yaml(self):
        schema = DbtModelSchema(
            name="test",
            columns=[
                DbtColumnSchema(
                    name="status",
                    data_tests=[
                        "not_null",
                        {
                            "accepted_values": {
                                "arguments": {"values": ["a", "b", "c"]},
                                "config": {"severity": "warn"},
                            }
                        },
                    ],
                ),
            ],
        )
        yaml_str = render_yaml(schema)
        assert "accepted_values:" in yaml_str
        assert "arguments:" in yaml_str
        assert "- a" in yaml_str
        assert "severity: warn" in yaml_str

    def test_quote_false_in_yaml(self):
        schema = DbtModelSchema(
            name="test",
            columns=[
                DbtColumnSchema(
                    name="cat_id",
                    data_tests=[
                        {
                            "accepted_values": {
                                "arguments": {"values": [1, 2, 3], "quote": False},
                                "config": {"severity": "warn"},
                            }
                        },
                    ],
                ),
            ],
        )
        yaml_str = render_yaml(schema)
        assert "quote: false" in yaml_str

    def test_multiline_description(self):
        schema = DbtModelSchema(
            name="test",
            description="Line one\nLine two",
            columns=[],
        )
        yaml_str = render_yaml(schema)
        # ruamel should use block scalar for multiline
        assert "Line one" in yaml_str
        assert "Line two" in yaml_str

    def test_meta_renders_under_config(self):
        schema = DbtModelSchema(
            name="test",
            columns=[
                DbtColumnSchema(name="empty", meta={"all_null_warning": True}),
            ],
        )
        yaml_str = render_yaml(schema)
        assert "config:" in yaml_str
        assert "meta:" in yaml_str
        assert "all_null_warning: true" in yaml_str

    def test_data_type_renders_at_column_level_in_order(self):
        schema = DbtModelSchema(
            name="test",
            columns=[
                DbtColumnSchema(
                    name="id",
                    description="Primary key",
                    data_type="INT64",
                    data_tests=["not_null"],
                    meta={"all_null_warning": True},
                ),
            ],
        )
        yaml_str = render_yaml(schema)
        assert "data_type: INT64" in yaml_str
        # Ordering: name → description → data_type → data_tests → config
        name_idx = yaml_str.index("name: id")
        desc_idx = yaml_str.index("description: Primary key")
        dt_idx = yaml_str.index("data_type: INT64")
        tests_idx = yaml_str.index("data_tests:")
        config_idx = yaml_str.index("config:")
        assert name_idx < desc_idx < dt_idx < tests_idx < config_idx

    def test_empty_description_still_present(self):
        schema = DbtModelSchema(
            name="test",
            description="",
            columns=[DbtColumnSchema(name="col")],
        )
        yaml_str = render_yaml(schema)
        assert "description: ''" in yaml_str or "description:" in yaml_str
