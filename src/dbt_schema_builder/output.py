"""dbt YAML schema models and serialisation."""

from __future__ import annotations

import sys
from typing import Any

from pydantic import BaseModel

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

from dbt_schema_builder.column_types import PassOneStats, ProfiledColumn


class DbtColumnSchema(BaseModel):
    """A single column entry in a dbt schema YAML."""

    name: str
    description: str = ""
    data_type: str = ""
    data_tests: list[str | dict] = []
    meta: dict = {}


class DbtModelSchema(BaseModel):
    """A model entry in a dbt schema YAML."""

    name: str
    description: str = ""
    columns: list[DbtColumnSchema] = []


def assemble_schema(
    model_name: str,
    table_description: str,
    columns: list[ProfiledColumn],
    pass_one_stats: dict[str, PassOneStats],
    pass_two_results: dict[str, Any],
    column_descriptions: dict[str, str],
    include_data_tests: bool = True,
) -> DbtModelSchema:
    """Assemble a DbtModelSchema from profiling results and descriptions."""
    dbt_columns: list[DbtColumnSchema] = []

    for col in columns:
        stats = pass_one_stats.get(col.column_name)
        if stats is None:
            # UnprofilableColumn won't have stats
            stats = PassOneStats(null_count=0, distinct_count=0, total_count=0)

        pass_two = pass_two_results.get(col.column_name)
        tests = col.to_dbt_tests(stats, pass_two) if include_data_tests else []
        meta = col.to_dbt_meta(stats)
        description = column_descriptions.get(col.column_name, "")

        dbt_columns.append(
            DbtColumnSchema(
                name=col.column_name,
                description=description,
                data_type=col.data_type,
                data_tests=tests,
                meta=meta,
            )
        )

    return DbtModelSchema(
        name=model_name,
        description=table_description,
        columns=dbt_columns,
    )


def _literal_block(text: str) -> LiteralScalarString:
    """Wrap text in a literal block scalar, ensuring a trailing newline so
    ruamel emits `|` (clip) rather than `|-` (strip)."""
    if not text.endswith("\n"):
        text = text + "\n"
    return LiteralScalarString(text)


def _to_commented_map(schema: DbtModelSchema) -> CommentedMap:
    """Convert a DbtModelSchema to a ruamel CommentedMap preserving key order."""
    model = CommentedMap()
    model["name"] = schema.name

    if schema.description:
        # Always use a literal block scalar so ruamel never falls back to
        # double-quoting (e.g. when the description contains `:`).
        model["description"] = _literal_block(schema.description)
    else:
        model["description"] = ""

    columns_seq = CommentedSeq()
    for col in schema.columns:
        col_map = CommentedMap()
        col_map["name"] = col.name

        if col.description:
            if "\n" in col.description:
                col_map["description"] = _literal_block(col.description)
            else:
                col_map["description"] = col.description
        else:
            col_map["description"] = ""

        if col.data_type:
            col_map["data_type"] = col.data_type
        if col.data_tests:
            col_map["data_tests"] = col.data_tests
        if col.meta:
            config = CommentedMap()
            config["meta"] = col.meta
            col_map["config"] = config

        columns_seq.append(col_map)

    model["columns"] = columns_seq
    return model


def render_yaml(schema: DbtModelSchema) -> str:
    """Render a DbtModelSchema to a dbt-compatible YAML string."""
    doc = CommentedMap()
    models_seq = CommentedSeq()
    models_seq.append(_to_commented_map(schema))
    doc["models"] = models_seq

    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 120
    yaml.indent(mapping=2, sequence=4, offset=2)

    import io

    stream = io.StringIO()
    yaml.dump(doc, stream)
    return stream.getvalue()


def print_yaml(schema: DbtModelSchema) -> None:
    """Print the schema YAML to stdout."""
    sys.stdout.write(render_yaml(schema))
