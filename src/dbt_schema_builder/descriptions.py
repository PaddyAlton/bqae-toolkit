"""Description resolution — resolves column and table descriptions from the dbt manifest and BigQuery metadata."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from google.cloud.bigquery import Client

from dbt_schema_builder.manifest import get_manifest_entry

logger = logging.getLogger(__name__)


def sanitise_description(raw: str) -> str:
    """Clean up a raw description string.

    - Replace literal ``\\n`` with real newlines.
    - Strip trailing whitespace per line.
    - Strip leading/trailing whitespace overall.
    """
    text = raw.replace("\\n", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


class DescriptionResolver:
    """Resolves table and column descriptions from the manifest and BigQuery."""

    def __init__(
        self,
        manifest: dict,
        project_name: str | None,
        dbt_project_dir: Path | None,
        bq_client: Client,
        bq_project: str,
    ) -> None:
        self.manifest = manifest
        self.project_name = project_name
        self.dbt_project_dir = dbt_project_dir
        self.bq_client = bq_client
        self.bq_project = bq_project

    def _get_entry(self, model_name: str) -> dict:
        if not self.project_name:
            return {}
        return get_manifest_entry(self.manifest, self.project_name, model_name)

    # -----------------------------------------------------------------------
    # Table description
    # -----------------------------------------------------------------------

    async def resolve_table(self, model_name: str, dataset: str, relation_name: str) -> str:
        """Resolve the table description.

        1. Check the manifest for an existing description (no-clobber).
        2. Fall back to INFORMATION_SCHEMA.TABLE_OPTIONS.
        """
        entry = self._get_entry(model_name)
        existing = entry.get("description", "")
        if existing.strip():
            return existing

        return await self._bq_table_description(dataset, relation_name)

    async def _bq_table_description(self, dataset: str, relation_name: str) -> str:
        sql = (
            f"SELECT option_value "
            f"FROM `{self.bq_project}.{dataset}.INFORMATION_SCHEMA.TABLE_OPTIONS` "
            f"WHERE table_name = '{relation_name}' AND option_name = 'description'"
        )
        try:
            rows = await asyncio.to_thread(lambda: list(self.bq_client.query(sql).result()))
            if rows:
                raw = rows[0]["option_value"]
                # BigQuery returns option_value as a SQL string literal — wrapped
                # in `"`s with `\n` etc. escaped. JSON decoding handles both.
                try:
                    raw = json.loads(raw)
                except (ValueError, TypeError):
                    pass
                return sanitise_description(raw)
        except Exception:
            logger.warning("Failed to fetch table description for %s.%s", dataset, relation_name, exc_info=True)
        return ""

    # -----------------------------------------------------------------------
    # Column description
    # -----------------------------------------------------------------------

    async def resolve_column(
        self,
        column_name: str,
        model_name: str,
        dataset: str,
        relation_name: str,
    ) -> str:
        """Resolve a column description.

        1. Check the manifest for an existing description (no-clobber).
        2. Trace lineage via sqlglot to find the upstream source.
        3. Fall back to BigQuery INFORMATION_SCHEMA.COLUMN_FIELD_PATHS.
        """
        entry = self._get_entry(model_name)
        columns = entry.get("columns", {})
        col_entry = columns.get(column_name, {})
        existing = col_entry.get("description", "")
        if existing.strip():
            return existing

        # Try lineage tracing
        upstream = self._trace_lineage(model_name, column_name)
        if upstream:
            up_dataset, up_table, up_column = upstream
            desc = await self._bq_column_description(up_dataset, up_table, up_column)
            if desc:
                return desc

        # Fall back to direct BQ lookup on the target table
        return await self._bq_column_description(dataset, relation_name, column_name)

    def _trace_lineage(self, model_name: str, column_name: str) -> tuple[str, str, str] | None:
        """Use sqlglot to trace column lineage from the compiled SQL.

        Returns (dataset, table, column) for the upstream source, or None.
        """
        if not self.dbt_project_dir or not self.project_name:
            return None

        entry = self._get_entry(model_name)
        if not entry:
            return None

        # Build the compiled SQL path
        original_path = entry.get("original_file_path", "")
        if not original_path:
            return None

        compiled_path = self.dbt_project_dir / "target" / "compiled" / self.project_name / original_path
        if not compiled_path.exists():
            logger.warning("Compiled SQL not found at %s — skipping lineage for %s", compiled_path, column_name)
            return None

        compiled_sql = compiled_path.read_text()

        try:
            from sqlglot.lineage import lineage

            result = lineage(column_name, compiled_sql, dialect="bigquery")
            # Walk downstream sources
            for node in result.walk():
                if node.source and node.source.name:
                    source_ref = node.source.name
                    parts = source_ref.replace("`", "").split(".")
                    if len(parts) >= 2:
                        return parts[-2], parts[-1], node.name
        except Exception:
            logger.warning("Lineage tracing failed for %s.%s", model_name, column_name, exc_info=True)

        return None

    async def _bq_column_description(self, dataset: str, table: str, column: str) -> str:
        sql = (
            f"SELECT description "
            f"FROM `{self.bq_project}.{dataset}.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS` "
            f"WHERE table_name = '{table}' AND column_name = '{column}'"
        )
        try:
            rows = await asyncio.to_thread(lambda: list(self.bq_client.query(sql).result()))
            if rows and rows[0]["description"]:
                return sanitise_description(rows[0]["description"])
        except Exception:
            logger.warning("Failed to fetch column description for %s.%s.%s", dataset, table, column, exc_info=True)
        return ""
