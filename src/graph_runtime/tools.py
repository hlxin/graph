"""GRAPH as tools: one small, vendor-neutral surface that agents and other programs call with JSON.

The same tools are reachable as Python (``GraphToolkit().call(name, arguments)``), on the command line
(``graph-runtime call <tool> '<json>'``) and over the Model Context Protocol (``graph-runtime mcp``, stdio). Each
tool is described by plain JSON Schema (:data:`TOOLS`), the common form of function calling.

Design rules (after SCALE's toolkit, from which this pattern comes):

* **Results are data.** Every tool returns a JSON-ready dict. A problem is returned as ``{"error": {"code",
  "message"}}``, never raised, so that a caller can read it and recover.
* **Nothing is written.** The tools read the files they are given (records, projection snapshots, saved indexes)
  and write nothing.
* **A session remembers what it built.** A projection (and an index) is kept under its identity, so that later
  calls name it instead of sending the records again. The session keeps the most recent ones (``KEEP``).
"""

from __future__ import annotations

import importlib.util
import json
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ._version import RUNTIME_NAME, __version__
from .engine import execute
from .errors import GraphRuntimeError
from .projection import CONTRACT, Projection
from .request import OPERATIONS
from .result import Result
from .revalidate import diff, revalidate
from .schemas import json_schema
from .verify import verify_result

KEEP = 16                         # projections and indexes a session keeps

_RECORDS = {
    "nodes": {"type": "array", "description": "Node IDs, or {id, types}."},
    "edges": {"type": "array", "description": "{id, source, target, relation}: parallel edges stay distinct."},
    "relations": {"type": "array", "items": {"type": "string"}, "description": "Declared relations (default: those "
                  "the edges use)."},
    "node_types": {"type": "array", "items": {"type": "string"}, "description": "Declared node types."},
    "type_parents": {"type": "object", "description": "{type: [parent types]}: a type matches its subtypes' nodes."},
    "relation_parents": {"type": "object", "description": "{relation: [parent relations]}: a scope on a relation "
                         "covers its specializations."},
    "bindings": {"type": "object", "description": "{name: text}: what the projection was built from (source, schema, "
                 "policy); part of its identity."},
}
_PROJECTION = {
    "type": "object",
    "description": "A projection: {identity} of one this session holds, the records inline, or {path} to a JSON file "
                   "of records or a projection snapshot.",
    "properties": {"identity": {"type": "string"}, "path": {"type": "string"}, **_RECORDS},
}

TOOLS: list[dict] = [
    {
        "name": "graph_status",
        "description": "Start here. The runtime and contract versions, the operations, whether optional text search is "
                       "available, and the projections and indexes this session holds.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "graph_describe",
        "description": "The contract's JSON Schema for request, result, projection or hypothesis_candidate (each "
                       "operation's parameters and answer are in request and result); with no name, the kinds and the "
                       "operations.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
    {
        "name": "graph_projection",
        "description": "Build an immutable projection from records (inline, or a JSON file of records or a snapshot) "
                       "and keep it in this session under its identity. Returns the identity and counts; with "
                       "snapshot true, the snapshot too.",
        "input_schema": {"type": "object", "properties": {
            **_RECORDS, "path": {"type": "string", "description": "A JSON file of records, or a projection snapshot."},
            "snapshot": {"type": "boolean"}}},
    },
    {
        "name": "graph_execute",
        "description": "Run a request (reachability, typed_path, shortest_path, bottleneck_path, dependency_impact, "
                       "pattern or support) on a projection: by default the one this session holds under the request's "
                       "projection identity. Returns the result: status, answer with witnesses (and certificates when "
                       "asked), limitations, result_id. An interrupted answer never certifies a negative.",
        "input_schema": {"type": "object", "required": ["request"], "properties": {
            "request": {"type": "object", "description": "{operation, ...parameters, limits}; projection defaults to "
                                                         "the projection given here."},
            "projection": _PROJECTION,
            "timeout": {"type": "number", "description": "Seconds; an interrupted result says so."}}},
    },
    {
        "name": "graph_verify",
        "description": "Check a result against a projection's records, independently of the engine: witnesses, the "
                       "closure of complete reachability and impact answers, certificates, support recomputed. "
                       "Returns the problems (none when it checks out).",
        "input_schema": {"type": "object", "required": ["result"], "properties": {
            "result": {"type": "object"}, "projection": _PROJECTION}},
    },
    {
        "name": "graph_revalidate",
        "description": "Whether cached results still hold on another projection (after the records changed): holds, "
                       "fails (naming the edit that refutes it) or unknown, without searching again. Give the results' "
                       "own projection as old to check only what the edit touched.",
        "input_schema": {"type": "object", "required": ["results", "projection"], "properties": {
            "results": {"type": "array", "items": {"type": "object"}}, "projection": _PROJECTION,
            "old": _PROJECTION}},
    },
    {
        "name": "graph_search",
        "description": "Exact text search (BM25, hashed vectors, hybrid or rank fusion) over items {id, text, record}, "
                       "inline or from a saved index; returns the hits and, with a projection, the nodes they name "
                       "(where a traversal starts). Needs numpy.",
        "input_schema": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object"}},
            "index": {"type": "string", "description": "A saved index's stem, or an index_id this session holds."},
            "k": {"type": "integer", "minimum": 1}, "mode": {"enum": ["hybrid", "vector", "keyword", "rrf"]},
            "records": {"type": "array", "items": {"type": "string"}, "description": "Search only these records' "
                                                                                    "items."},
            "aliases": {"type": "object"}, "variants": {"type": "object"},
            "projection": _PROJECTION}},
    },
    {
        "name": "graph_conformance",
        "description": "Run the language-neutral conformance suite against this runtime.",
        "input_schema": {"type": "object", "properties": {}},
    },
]
TOOL_NAMES = tuple(t["name"] for t in TOOLS)


def is_error(result: Mapping) -> bool:
    """Whether a tool's result is its error (``{"error": {...}}``), as opposed to data that holds one, such as a
    failed request's result (which says so in its status)."""
    return set(result) == {"error"} and isinstance(result["error"], Mapping)


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class GraphToolkit:
    """The tools, with a session that keeps the projections and indexes it built."""

    def __init__(self) -> None:
        self.projections: OrderedDict[str, Projection] = OrderedDict()
        self.indexes: OrderedDict[str, Any] = OrderedDict()

    def call(self, name: str, arguments: Mapping | None = None) -> dict:
        """Run tool ``name`` with ``arguments``; a problem comes back as ``{"error": {...}}``."""
        if name not in TOOL_NAMES:
            return {"error": {"code": "unknown_tool", "message": f"no tool {name!r}; tools: {', '.join(TOOL_NAMES)}"}}
        if arguments is not None and not isinstance(arguments, Mapping):
            return {"error": {"code": "invalid_arguments", "message": "arguments are a JSON object"}}
        try:
            return getattr(self, "_" + name)(**dict(arguments or {}))
        except ToolError as e:
            return {"error": {"code": e.code, "message": e.message}}
        except GraphRuntimeError as e:
            return {"error": {"code": e.code, "message": e.message}}
        except TypeError as e:                                  # an unexpected or missing argument
            return {"error": {"code": "invalid_arguments", "message": str(e)}}
        except (OSError, ValueError) as e:
            return {"error": {"code": "invalid_input", "message": f"{type(e).__name__}: {e}"}}
        except Exception as e:  # noqa: BLE001 - a tool answers, it never takes its caller down
            return {"error": {"code": "internal_error", "message": f"{type(e).__name__}: {e}"}}

    # ------------------------------------------------------------------------------------------ session
    def _keep(self, table: OrderedDict, key: str, value: Any) -> Any:
        table[key] = value
        table.move_to_end(key)
        while len(table) > KEEP:
            table.popitem(last=False)
        return value

    def projection(self, ref: Mapping | None, identity: str | None = None) -> Projection:
        """The projection a reference names: one this session holds, inline records, or a file."""
        ref = dict(ref or {})
        if not ref:
            if identity in self.projections:
                return self.projections[identity]
            raise ToolError("unknown_projection", f"this session holds no projection {identity!r}: build it first "
                                                  "with graph_projection, or give the records")
        if set(ref) == {"identity"}:
            if ref["identity"] in self.projections:
                return self.projections[ref["identity"]]
            raise ToolError("unknown_projection", f"this session holds no projection {ref['identity']!r}")
        if "path" in ref:
            if set(ref) != {"path"}:
                raise ToolError("invalid_arguments", "give a projection's path or its records, not both")
            ref = json.loads(Path(ref["path"]).expanduser().read_text(encoding="utf-8"))
        if ref.get("kind") == "graph_runtime.projection":
            p = Projection.from_dict(ref)
        else:
            stray = sorted(set(ref) - set(_RECORDS))
            if stray:
                raise ToolError("invalid_arguments", f"records take {sorted(_RECORDS)}, not {stray}")
            if "nodes" not in ref or "edges" not in ref:
                raise ToolError("invalid_arguments", "records need nodes and edges")
            p = Projection.build(**ref)
        return self._keep(self.projections, p.identity, p)

    # ------------------------------------------------------------------------------------------ tools
    def _graph_status(self) -> dict:
        search = importlib.util.find_spec("numpy") is not None       # without loading it
        return {"runtime": {"name": RUNTIME_NAME, "version": __version__}, "contract": CONTRACT,
                "operations": list(OPERATIONS), "text_search": search,
                "projections": [{"identity": i, "nodes": p.node_count, "edges": p.edge_count}
                                for i, p in self.projections.items()],
                "indexes": [{"index_id": i, "items": len(ix)} for i, ix in self.indexes.items()],
                "tools": list(TOOL_NAMES)}

    def _graph_describe(self, name: str | None = None) -> dict:
        from .schemas import ENTRIES
        if name is None:
            return {"kinds": list(ENTRIES), "operations": list(OPERATIONS), "contract": CONTRACT}
        if name not in ENTRIES:
            raise ToolError("unknown_kind", f"the contract describes {list(ENTRIES)}, not {name!r}")
        return {"name": name, "schema": json_schema(name)}

    def _graph_projection(self, path: str | None = None, snapshot: bool = False, **records: Any) -> dict:
        ref = {**records, **({"path": path} if path is not None else {})}
        if not ref:
            raise ToolError("invalid_arguments", "give the records (nodes and edges) or a path")
        p = self.projection(ref)
        out = {"identity": p.identity, "nodes": p.node_count, "edges": p.edge_count, "relations": list(p.relations),
               "node_types": list(p.node_types), "bindings": dict(p.bindings)}
        if snapshot:
            out["snapshot"] = p.to_dict()
        return out

    def _graph_execute(self, request: Mapping, projection: Mapping | None = None,
                       timeout: float | None = None) -> dict:
        if not isinstance(request, Mapping):
            raise ToolError("invalid_arguments", "request is a JSON object")
        p = self.projection(projection, request.get("projection"))
        res = execute(p, {**request, "projection": request.get("projection") or p.identity}, timeout=timeout)
        return res.to_dict()

    def _graph_verify(self, result: Mapping, projection: Mapping | None = None) -> dict:
        p = self.projection(projection, result.get("projection") if isinstance(result, Mapping) else None)
        snap = p.to_dict()
        problems = verify_result(result, nodes=snap["nodes"], edges=snap["edges"], type_parents=snap["type_parents"],
                                 relation_parents=snap["relation_parents"])
        return {"problems": problems, "checked_against": p.identity}

    def _graph_revalidate(self, results: list, projection: Mapping, old: Mapping | None = None) -> dict:
        if not isinstance(results, list):
            raise ToolError("invalid_arguments", "results is a list of results")
        new = self.projection(projection)
        d = diff(self.projection(old), new) if old else None
        checked = [Result.from_dict(r) for r in results]      # a result that does not match its result_id is refused
        return {"revalidations": [revalidate(r, new, diff=d).to_dict() for r in checked],
                **({"diff": d.to_dict()} if d is not None else {})}

    def _graph_search(self, query: str, items: list | None = None, index: str | None = None, k: int = 10,
                      mode: str = "hybrid", records: list | None = None, aliases: Mapping | None = None,
                      variants: Mapping | None = None, projection: Mapping | None = None) -> dict:
        from .retrieval import HashEmbedder, Index, Tokenizer, seeds
        if (items is None) == (index is None):
            raise ToolError("invalid_arguments", "give items or an index, one of them")
        if index is not None:
            ix = self.indexes.get(index)
            if ix is None:
                ix = Index.load(Path(index).expanduser())
        else:
            words = Tokenizer(aliases=aliases, variants=variants)
            ix = Index.build(items, tokenizer=words, embedder=HashEmbedder(words))
        self._keep(self.indexes, ix.index_id, ix)
        hits = ix.search(query, k=k, mode=mode, records=records)
        out = {"index_id": ix.index_id, "embedder": ix.embedder_id, "query": query, "mode": mode, "hits": hits}
        if projection is not None:
            out["seeds"] = seeds(hits, self.projection(projection))
        return out

    def _graph_conformance(self) -> dict:
        from .conformance import run_conformance
        return run_conformance()
