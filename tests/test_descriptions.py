"""Tests for description resolution and sanitisation."""

from __future__ import annotations

from dbt_schema_builder.descriptions import sanitise_description


class TestSanitiseDescription:
    def test_literal_newlines(self):
        assert sanitise_description("line1\\nline2") == "line1\nline2"

    def test_strips_trailing_whitespace(self):
        assert sanitise_description("hello   \nworld  ") == "hello\nworld"

    def test_strips_overall_whitespace(self):
        assert sanitise_description("  trimmed  ") == "trimmed"

    def test_combined(self):
        raw = "  first line  \\n  second line  \\n  "
        result = sanitise_description(raw)
        assert result == "first line\n  second line"

    def test_empty_string(self):
        assert sanitise_description("") == ""
