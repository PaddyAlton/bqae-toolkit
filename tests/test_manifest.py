"""Tests for manifest loading and project name discovery."""

from __future__ import annotations

import json
from pathlib import Path

from dbt_schema_builder.manifest import discover_project_name, get_manifest_entry, load_manifest


class TestDiscoverProjectName:
    def test_reads_name(self, tmp_path: Path):
        project_yml = tmp_path / "dbt_project.yml"
        project_yml.write_text("name: my_dbt_project\nversion: '1.0'\n")
        assert discover_project_name(tmp_path) == "my_dbt_project"

    def test_missing_file(self, tmp_path: Path):
        assert discover_project_name(tmp_path) is None


class TestLoadManifest:
    def test_loads_json(self, tmp_path: Path):
        target = tmp_path / "target"
        target.mkdir()
        manifest = {"nodes": {"model.proj.my_model": {"description": "hi"}}}
        (target / "manifest.json").write_text(json.dumps(manifest))
        result = load_manifest(tmp_path)
        assert result["nodes"]["model.proj.my_model"]["description"] == "hi"

    def test_missing_manifest(self, tmp_path: Path):
        result = load_manifest(tmp_path)
        assert result == {}


class TestGetManifestEntry:
    def test_found(self):
        manifest = {"nodes": {"model.proj.my_model": {"description": "A model"}}}
        entry = get_manifest_entry(manifest, "proj", "my_model")
        assert entry["description"] == "A model"

    def test_not_found(self):
        manifest = {"nodes": {}}
        assert get_manifest_entry(manifest, "proj", "missing") == {}

    def test_empty_manifest(self):
        assert get_manifest_entry({}, "proj", "model") == {}
