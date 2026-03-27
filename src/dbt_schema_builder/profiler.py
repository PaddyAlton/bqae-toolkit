"""Two-pass profiling engine for BigQuery tables."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from google.cloud.bigquery import Client

from dbt_schema_builder.column_types import PassOneStats, ProfiledColumn


def _coerce_bq_value(value: Any) -> Any:
    """Convert BigQuery-specific Python types to plain Python types.

    BigQuery returns NUMERIC/BIGNUMERIC as Decimal, which ruamel.yaml
    cannot serialise. Convert to int if lossless, otherwise float.
    """
    if isinstance(value, Decimal):
        if value == int(value):
            return int(value)
        return float(value)
    return value


def _build_pass_one_sql(columns: list[ProfiledColumn], table_ref: str) -> str:
    """Build the single aggregate query for pass 1."""
    expressions: list[str] = ["COUNT(*) AS __total_count"]
    for col in columns:
        expressions.extend(col.pass_one_expressions())
    select_clause = ",\n  ".join(expressions)
    return f"SELECT\n  {select_clause}\nFROM {table_ref}"


def _parse_pass_one_row(row: dict[str, Any], columns: list[ProfiledColumn]) -> dict[str, PassOneStats]:
    """Parse the flat pass-1 result row into per-column PassOneStats."""
    total_count = row["__total_count"]
    stats: dict[str, PassOneStats] = {}

    for col in columns:
        prefix = f"{col.column_name}__"
        null_count = row.get(f"{prefix}null_count", 0)
        distinct_count = row.get(f"{prefix}distinct_count", 0)

        extra: dict[str, Any] = {}
        for key, value in row.items():
            if key.startswith(prefix) and key not in (f"{prefix}null_count", f"{prefix}distinct_count"):
                extra_key = key[len(prefix) :]
                extra[extra_key] = _coerce_bq_value(value)

        stats[col.column_name] = PassOneStats(
            null_count=null_count,
            distinct_count=distinct_count,
            total_count=total_count,
            extra=extra,
        )

    return stats


def execute_pass_one(
    columns: list[ProfiledColumn],
    table_ref: str,
    bq_client: Client,
) -> dict[str, PassOneStats]:
    """Execute the pass-1 query and return per-column statistics."""
    sql = _build_pass_one_sql(columns, table_ref)
    result = bq_client.query(sql).result()
    row = dict(next(iter(result)))
    return _parse_pass_one_row(row, columns)


async def run_profiling(
    columns: list[ProfiledColumn],
    table_ref: str,
    bq_client: Client,
) -> tuple[dict[str, PassOneStats], dict[str, Any]]:
    """Run the two-pass profiling pipeline.

    Pass 1 runs synchronously (single query). Pass 2 dispatches concurrent
    queries via asyncio.to_thread for columns that need it.
    """
    pass_one_stats = await asyncio.to_thread(execute_pass_one, columns, table_ref, bq_client)

    async def _pass_two(col: ProfiledColumn) -> tuple[str, Any]:
        sql = col.pass_two_query(pass_one_stats[col.column_name], table_ref)
        if sql is None:
            return col.column_name, None
        rows = await asyncio.to_thread(lambda s=sql: list(bq_client.query(s).result()))
        return col.column_name, [_coerce_bq_value(list(r.values())[0]) for r in rows]

    tasks = [_pass_two(c) for c in columns if c.needs_pass_two(pass_one_stats[c.column_name])]

    if tasks:
        results = await asyncio.gather(*tasks)
        pass_two_results = dict(results)
    else:
        pass_two_results = {}

    return pass_one_stats, pass_two_results
