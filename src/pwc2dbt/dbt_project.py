"""Write compiled models as a dbt project.

Only COMPLETE models go under ``models/``. INCOMPLETE models go to
``incomplete/``, outside the model paths, so dbt never builds them.
"""

import json
import re
import shutil
from pathlib import Path

from .compiler import COMPLETE, ModelResult
from .ir import Folder
from .sql import render_model

OWNED_DIRS = ("models", "incomplete")


class OutputError(Exception):
    """The output directory cannot be written without losing files."""


def _q(value: str) -> str:
    """Quote a YAML scalar; JSON strings are valid YAML."""
    return json.dumps(value)


def project_name(folder: Folder) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", folder.name.lower())
    return name if re.match(r"[a-z_]", name) else f"p_{name}"


def write_project(results: list[ModelResult], folder: Folder, out_dir: Path, overwrite: bool) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        if not overwrite:
            raise OutputError(f"output directory {out_dir} is not empty; pass --overwrite to replace its models")
        for owned in OWNED_DIRS:  # stale models from an earlier run must not survive
            shutil.rmtree(out_dir / owned, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    name = project_name(folder)
    (out_dir / "dbt_project.yml").write_text(
        f"name: {_q(name)}\n"
        'version: "1.0.0"\n'
        "config-version: 2\n"
        f"profile: {_q(name)}\n"
        'model-paths: ["models"]\n'
        "\n"
        "models:\n"
        f"  {name}:\n"
        "    # Every target is truncated and reloaded by its session (Pre SQL TRUNCATE).\n"
        "    +materialized: table\n"
    )

    complete = [r for r in results if r.status == COMPLETE]
    for r in results:
        path = out_dir / ("models" if r.status == COMPLETE else "incomplete")
        if r.status == COMPLETE:
            path = path / r.mapping.lower()
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{r.name}.sql").write_text(render_model(r))

    for mapping in dict.fromkeys(r.mapping for r in complete):
        lines = ["version: 2", "", "models:"]
        for r in (r for r in complete if r.mapping == mapping):
            lines += [
                f"  - name: {_q(r.name)}",
                f"    description: {_q(f'Target {r.target_instance} loaded by PowerCenter mapping {r.mapping}.')}",
                "    columns:",
            ]
            for column, data_type in r.columns:
                lines += [f"      - name: {_q(column)}", f"        data_type: {_q(data_type)}"]
        (out_dir / "models" / mapping.lower() / "schema.yml").write_text("\n".join(lines) + "\n")

    sources = sorted({s for r in complete for s in r.sources})
    if sources:
        lines = ["version: 2", "", "sources:"]
        for owner in dict.fromkeys(owner for owner, _ in sources):
            lines += [f"  - name: {_q(owner)}", f"    schema: {_q(owner)}", "    tables:"]
            lines += [f"      - name: {_q(table)}" for o, table in sources if o == owner]
        (out_dir / "models" / "sources.yml").write_text("\n".join(lines) + "\n")
