"""Manifest reading utilities — loading manifest.json and discovering the dbt project name."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)


def discover_project_name(dbt_project_dir: Path) -> str | None:
    """Read the project name from dbt_project.yml."""
    project_yml = dbt_project_dir / "dbt_project.yml"
    if not project_yml.exists():
        logger.warning("dbt_project.yml not found at %s — skipping manifest lookups", dbt_project_dir)
        return None

    yaml = YAML()
    with open(project_yml) as f:
        data = yaml.load(f)

    return data.get("name")


def load_manifest(dbt_project_dir: Path) -> dict:
    """Load target/manifest.json from the dbt project directory.

    Returns an empty dict (with a warning) if the file doesn't exist.
    """
    manifest_path = dbt_project_dir / "target" / "manifest.json"
    if not manifest_path.exists():
        logger.warning("manifest.json not found at %s — descriptions will be empty", manifest_path)
        return {}

    with open(manifest_path) as f:
        return json.load(f)


def get_manifest_entry(manifest: dict, project_name: str, model_name: str) -> dict:
    """Look up a model node in the manifest.

    The node key format is ``model.{project_name}.{model_name}``.
    Returns the entry dict, or ``{}`` if not found.
    """
    if not manifest:
        return {}

    node_key = f"model.{project_name}.{model_name}"
    nodes = manifest.get("nodes", {})
    return nodes.get(node_key, {})
