"""Async coordination of profiling, description resolution, and schema assembly."""

from __future__ import annotations

import asyncio
from typing import Any

from google.cloud.bigquery import Client

from dbt_schema_builder.column_types import ProfiledColumn
from dbt_schema_builder.descriptions import DescriptionResolver
from dbt_schema_builder.output import DbtModelSchema, assemble_schema
from dbt_schema_builder.profiler import execute_pass_one


async def orchestrate(
    columns: list[ProfiledColumn],
    table_ref: str,
    model_name: str,
    dataset: str,
    relation_name: str,
    resolver: DescriptionResolver,
    bq_client: Client,
    include_data_tests: bool = True,
) -> DbtModelSchema:
    """Run profiling and description resolution, then assemble the output schema.

    Sequencing:
    - Pass 1 runs first (single query, required by pass 2).
    - After pass 1 completes, pass 2 queries and all description lookups
      run concurrently for maximum parallelism.
    """
    # Pass 1: synchronous, single query
    pass_one_stats = await asyncio.to_thread(execute_pass_one, columns, table_ref, bq_client)

    # Pass 2 + descriptions: all concurrent
    all_tasks: list[asyncio.Task] = []

    # Table description
    all_tasks.append(asyncio.create_task(resolver.resolve_table(model_name, dataset, relation_name)))

    # Column descriptions
    for col in columns:
        all_tasks.append(
            asyncio.create_task(resolver.resolve_column(col.column_name, model_name, dataset, relation_name))
        )

    # Pass 2 queries (only needed when including data tests)
    columns_needing_pass_two = (
        [c for c in columns if c.needs_pass_two(pass_one_stats[c.column_name])]
        if include_data_tests
        else []
    )

    async def _run_pass_two(col: ProfiledColumn) -> tuple[str, Any]:
        sql = col.pass_two_query(pass_one_stats[col.column_name], table_ref)
        if sql is None:
            return col.column_name, None
        rows = await asyncio.to_thread(lambda s=sql: list(bq_client.query(s).result()))
        return col.column_name, [list(r.values())[0] for r in rows]

    for col in columns_needing_pass_two:
        all_tasks.append(asyncio.create_task(_run_pass_two(col)))

    results = await asyncio.gather(*all_tasks)

    # Unpack by known offsets
    n_columns = len(columns)
    table_description: str = results[0]
    column_descriptions = {col.column_name: results[1 + i] for i, col in enumerate(columns)}
    pass_two_results = {name: value for name, value in results[1 + n_columns :]}

    return assemble_schema(
        model_name=model_name,
        table_description=table_description,
        columns=columns,
        pass_one_stats=pass_one_stats,
        pass_two_results=pass_two_results,
        column_descriptions=column_descriptions,
        include_data_tests=include_data_tests,
    )
