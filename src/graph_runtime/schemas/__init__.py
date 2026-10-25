"""The JSON Schema (draft 2020-12) of the contract: requests, results, hypothesis candidates, projection snapshots."""

from __future__ import annotations

import json
from importlib import resources

FILE = "graph-runtime-v0.schema.json"
ENTRIES = ("request", "result", "hypothesis_candidate", "projection")


def json_schema(entry: str | None = None) -> dict:
    """The contract's schema document, or, with ``entry`` (one of :data:`ENTRIES`), a schema that validates that
    record and resolves every definition it uses."""
    doc = json.loads(resources.files(__name__).joinpath(FILE).read_text(encoding="utf-8"))
    if entry is None:
        return doc
    if entry not in ENTRIES:
        raise KeyError(f"the contract schema defines {list(ENTRIES)}, not {entry!r}")
    return {"$schema": doc["$schema"], "$ref": f"#/$defs/{entry}", "$defs": doc["$defs"]}
