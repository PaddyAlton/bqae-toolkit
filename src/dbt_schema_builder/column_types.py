"""Discriminated union of column types with per-type profiling logic and test generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Union

from pydantic import BaseModel, Tag, Discriminator

from dbt_schema_builder.constants import MAX_ACCEPTED_VALUES


@dataclass
class PassOneStats:
    """Aggregated statistics from the first profiling pass."""

    null_count: int
    distinct_count: int
    total_count: int
    extra: dict[str, Any] = field(default_factory=dict)


def column_type_discriminator(raw: dict) -> str:
    """Map a raw INFORMATION_SCHEMA row to a column-type tag."""
    dt = raw.get("data_type", "").upper()
    if dt.startswith("ARRAY") or dt.startswith("STRUCT"):
        return "unprofilable"
    mapping = {
        "STRING": "string",
        "INT64": "integer",
        "INTEGER": "integer",
        "FLOAT64": "float",
        "NUMERIC": "float",
        "BIGNUMERIC": "float",
        "BOOL": "boolean",
        "BOOLEAN": "boolean",
        "TIMESTAMP": "timestamp",
        "DATETIME": "timestamp",
        "DATE": "timestamp",
        "JSON": "unprofilable",
        "GEOGRAPHY": "unprofilable",
    }
    return mapping.get(dt, "fallback")


# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------


class ColumnProfileBase(BaseModel):
    """Base for all column-type variants."""

    column_name: str
    data_type: str
    ordinal_position: int
    max_accepted_values: int = MAX_ACCEPTED_VALUES

    def pass_one_expressions(self) -> list[str]:
        """SQL expressions for the first profiling pass.

        Every variant gets null_count and distinct_count by default.
        Subclasses extend this with type-specific expressions.
        """
        col = self.column_name
        prefix = f"{col}__"
        return [
            f"COUNTIF({col} IS NULL) AS {prefix}null_count",
            f"COUNT(DISTINCT {col}) AS {prefix}distinct_count",
        ]

    def needs_pass_two(self, stats: PassOneStats) -> bool:
        """Whether this column requires a second-pass query."""
        return False

    def pass_two_query(self, stats: PassOneStats, table_ref: str) -> str | None:
        """Return the SQL for a second-pass query, or None."""
        return None

    def to_dbt_tests(self, stats: PassOneStats, pass_two_result: Any = None) -> list[str | dict]:
        """Return the list of dbt data_tests for this column."""
        if stats.null_count == stats.total_count:
            return []
        tests: list[str | dict] = []
        if stats.null_count == 0:
            tests.append("not_null")
        if stats.distinct_count == stats.total_count:
            tests.append("unique")
        return tests

    def to_dbt_meta(self, stats: PassOneStats) -> dict:
        """Return dbt meta dict entries for this column."""
        meta: dict[str, Any] = {}
        if stats.null_count == stats.total_count:
            meta["all_null_warning"] = True
        return meta


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------


class StringColumn(ColumnProfileBase):
    """STRING columns — pass 2 fetches accepted values for low-cardinality."""

    def pass_one_expressions(self) -> list[str]:
        col = self.column_name
        prefix = f"{col}__"
        return [
            *super().pass_one_expressions(),
            f"MAX(LENGTH({col})) AS {prefix}max_length",
        ]

    def needs_pass_two(self, stats: PassOneStats) -> bool:
        return 0 < stats.distinct_count <= self.max_accepted_values

    def pass_two_query(self, stats: PassOneStats, table_ref: str) -> str | None:
        if not self.needs_pass_two(stats):
            return None
        col = self.column_name
        return f"SELECT DISTINCT {col} FROM {table_ref} WHERE {col} IS NOT NULL ORDER BY {col}"

    def to_dbt_tests(self, stats: PassOneStats, pass_two_result: Any = None) -> list[str | dict]:
        if stats.null_count == stats.total_count:
            return []
        tests = super().to_dbt_tests(stats, pass_two_result)
        if pass_two_result is not None:
            tests.append(
                {
                    "accepted_values": {
                        "arguments": {"values": pass_two_result},
                        "config": {"severity": "warn"},
                    }
                }
            )
        return tests


class IntegerColumn(ColumnProfileBase):
    """INT64/INTEGER columns — pass 1 gets min/max, pass 2 fetches accepted values."""

    def pass_one_expressions(self) -> list[str]:
        col = self.column_name
        prefix = f"{col}__"
        return [
            *super().pass_one_expressions(),
            f"MIN({col}) AS {prefix}min_value",
            f"MAX({col}) AS {prefix}max_value",
        ]

    def needs_pass_two(self, stats: PassOneStats) -> bool:
        return 0 < stats.distinct_count <= self.max_accepted_values

    def pass_two_query(self, stats: PassOneStats, table_ref: str) -> str | None:
        if not self.needs_pass_two(stats):
            return None
        col = self.column_name
        return f"SELECT DISTINCT {col} FROM {table_ref} WHERE {col} IS NOT NULL ORDER BY {col}"

    def to_dbt_tests(self, stats: PassOneStats, pass_two_result: Any = None) -> list[str | dict]:
        if stats.null_count == stats.total_count:
            return []
        tests = super().to_dbt_tests(stats, pass_two_result)
        if pass_two_result is not None:
            tests.append(
                {
                    "accepted_values": {
                        "arguments": {"values": pass_two_result, "quote": False},
                        "config": {"severity": "warn"},
                    }
                }
            )
        min_val = stats.extra.get("min_value")
        max_val = stats.extra.get("max_value")
        if min_val is not None and max_val is not None:
            tests.append(
                {
                    "dbt_utils.accepted_range": {
                        "arguments": {"min_value": min_val, "max_value": max_val},
                        "config": {"severity": "warn"},
                    },
                }
            )
        return tests


class FloatColumn(ColumnProfileBase):
    """FLOAT64/NUMERIC/BIGNUMERIC columns — never pass 2, range test only."""

    def pass_one_expressions(self) -> list[str]:
        col = self.column_name
        prefix = f"{col}__"
        return [
            *super().pass_one_expressions(),
            f"MIN({col}) AS {prefix}min_value",
            f"MAX({col}) AS {prefix}max_value",
        ]

    def to_dbt_tests(self, stats: PassOneStats, pass_two_result: Any = None) -> list[str | dict]:
        if stats.null_count == stats.total_count:
            return []
        tests: list[str | dict] = []
        if stats.null_count == 0:
            tests.append("not_null")
        # floats are unlikely to be unique in a meaningful way — skip unique test
        min_val = stats.extra.get("min_value")
        max_val = stats.extra.get("max_value")
        if min_val is not None and max_val is not None:
            tests.append(
                {
                    "dbt_utils.accepted_range": {
                        "arguments": {"min_value": min_val, "max_value": max_val},
                        "config": {"severity": "warn"},
                    },
                }
            )
        return tests


class BooleanColumn(ColumnProfileBase):
    """BOOL/BOOLEAN columns."""

    def pass_one_expressions(self) -> list[str]:
        col = self.column_name
        prefix = f"{col}__"
        return [
            *super().pass_one_expressions(),
            f"COUNTIF({col} IS TRUE) AS {prefix}true_count",
        ]

    def to_dbt_tests(self, stats: PassOneStats, pass_two_result: Any = None) -> list[str | dict]:
        if stats.null_count == stats.total_count:
            return []
        tests: list[str | dict] = []
        if stats.null_count == 0:
            tests.append("not_null")
        return tests


class TimestampColumn(ColumnProfileBase):
    """TIMESTAMP/DATETIME/DATE columns — range test with date literals."""

    def pass_one_expressions(self) -> list[str]:
        col = self.column_name
        prefix = f"{col}__"
        return [
            *super().pass_one_expressions(),
            f"MIN({col}) AS {prefix}min_value",
            f"MAX({col}) AS {prefix}max_value",
        ]

    def to_dbt_tests(self, stats: PassOneStats, pass_two_result: Any = None) -> list[str | dict]:
        if stats.null_count == stats.total_count:
            return []
        tests: list[str | dict] = []
        if stats.null_count == 0:
            tests.append("not_null")
        min_val = stats.extra.get("min_value")
        max_val = stats.extra.get("max_value")
        if min_val is not None and max_val is not None:
            dt = self.data_type.upper()
            if dt == "DATE":
                min_lit = f"'{min_val}'"
                max_lit = f"'{max_val}'"
            else:
                min_lit = f"'{min_val}'"
                max_lit = f"'{max_val}'"
            tests.append(
                {
                    "dbt_utils.accepted_range": {
                        "arguments": {"min_value": min_lit, "max_value": max_lit},
                        "config": {"severity": "warn"},
                    },
                }
            )
        return tests


class UnprofilableColumn(ColumnProfileBase):
    """JSON, GEOGRAPHY, ARRAY*, STRUCT* — no profiling or tests."""

    def pass_one_expressions(self) -> list[str]:
        return []

    def to_dbt_tests(self, stats: PassOneStats, pass_two_result: Any = None) -> list[str | dict]:
        return []

    def to_dbt_meta(self, stats: PassOneStats) -> dict:
        # Override the base so the dummy stats (0,0,0) used for unprofilable
        # columns don't trip the all-null check.
        return {}


class FallbackColumn(ColumnProfileBase):
    """Any unrecognised type — basic not_null only."""

    pass


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------

ProfiledColumn = Annotated[
    Union[
        Annotated[StringColumn, Tag("string")],
        Annotated[IntegerColumn, Tag("integer")],
        Annotated[FloatColumn, Tag("float")],
        Annotated[BooleanColumn, Tag("boolean")],
        Annotated[TimestampColumn, Tag("timestamp")],
        Annotated[UnprofilableColumn, Tag("unprofilable")],
        Annotated[FallbackColumn, Tag("fallback")],
    ],
    Discriminator(column_type_discriminator),
]
