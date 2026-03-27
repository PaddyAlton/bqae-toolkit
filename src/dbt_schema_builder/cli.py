"""Command-line interface for dbt_schema_builder."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import cyclopts

from pydantic import TypeAdapter

from dbt_schema_builder.column_types import ProfiledColumn
from dbt_schema_builder.constants import MAX_ACCEPTED_VALUES
from dbt_schema_builder.descriptions import DescriptionResolver
from dbt_schema_builder.manifest import discover_project_name, load_manifest
from dbt_schema_builder.orchestrator import orchestrate
from dbt_schema_builder.output import print_yaml

logger = logging.getLogger(__name__)

app = cyclopts.App(
    name="dbt-schema-builder",
    help="Profile a BigQuery table and generate a dbt-compatible YAML schema block.",
)

profiled_column_adapter = TypeAdapter(list[ProfiledColumn])


def _fetch_information_schema_rows(bq_client, bq_project: str, dataset: str, relation_name: str) -> list[dict]:
    """Query INFORMATION_SCHEMA.COLUMNS for the target table."""
    sql = (
        f"SELECT column_name, data_type, ordinal_position "
        f"FROM `{bq_project}.{dataset}.INFORMATION_SCHEMA.COLUMNS` "
        f"WHERE table_name = '{relation_name}' "
        f"ORDER BY ordinal_position"
    )
    rows = list(bq_client.query(sql).result())
    return [dict(r) for r in rows]


@app.default
def schema_builder(
    dataset: str,
    relation_name: str,
    *,
    dbt_project_dir: Path | None = None,
    bq_project: str | None = None,
    max_accepted_values: int = MAX_ACCEPTED_VALUES,
) -> None:
    """Profile a BigQuery table and output a dbt YAML schema block."""
    from google.cloud.bigquery import Client

    if dbt_project_dir is None:
        env_val = os.environ.get("DBT_PROJECT_DIRECTORY")
        dbt_project_dir = Path(env_val) if env_val else Path(".")

    bq_client = Client(project=bq_project)
    resolved_project = bq_client.project

    # Load dbt project metadata
    project_name = discover_project_name(dbt_project_dir)
    manifest = load_manifest(dbt_project_dir)

    # Fetch column metadata from INFORMATION_SCHEMA
    info_rows = _fetch_information_schema_rows(bq_client, resolved_project, dataset, relation_name)
    if not info_rows:
        logger.error("No columns found for %s.%s.%s", resolved_project, dataset, relation_name)
        raise SystemExit(1)

    # Parse into discriminated union
    for row in info_rows:
        row["max_accepted_values"] = max_accepted_values
    columns: list[ProfiledColumn] = profiled_column_adapter.validate_python(info_rows)

    table_ref = f"`{resolved_project}.{dataset}.{relation_name}`"
    model_name = relation_name

    resolver = DescriptionResolver(
        manifest=manifest,
        project_name=project_name,
        dbt_project_dir=dbt_project_dir,
        bq_client=bq_client,
        bq_project=resolved_project,
    )

    schema = asyncio.run(
        orchestrate(
            columns=columns,
            table_ref=table_ref,
            model_name=model_name,
            dataset=dataset,
            relation_name=relation_name,
            resolver=resolver,
            bq_client=bq_client,
        )
    )

    print_yaml(schema)


def main() -> None:
    """Entry point."""
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
    app()
