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
* **Answers come in the size a reader needs.** An agent reads results into a limited context, so ``graph_execute``
  and ``graph_batch`` can return a ``summary`` of a result (its status, counts and the first items of its answer)
  instead of all of it; the session keeps the result (``KEEP_RESULTS``) under its ``result_id``, and ``graph_result``
  gives it whole, a slice of one of its lists, or one node's witness, when they are wanted. A request run again on
  the same projection, with no deadline, is answered from the session: results are reproducible, so the result is
  the same one.
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
from .periodic import PeriodicProjection
from .projection import CONTRACT, Projection
from .request import OPERATIONS, normalize_request
from .result import Result
from .revalidate import diff, revalidate
from .schemas import json_schema
from .verify import verify_result

KEEP = 16                         # projections and indexes a session keeps
KEEP_RESULTS = 64                 # results a session keeps, by result_id
SUMMARY_ITEMS = 20                # items of each list a summary shows

_RECORDS = {
    "nodes": {"type": "array", "description": "Node IDs, or {id, types}."},
    "edges": {"type": "array", "description": "{id, source, target, relation}: parallel edges stay distinct. With a "
              "shift (a list of integers) on every edge, the records are one cell of an infinite periodic graph, "
              "whose nodes are named <node>@<k1>,...,<kd>."},
    "relations": {"type": "array", "items": {"type": "string"}, "description": "Declared relations (default: those "
                  "the edges use)."},
    "node_types": {"type": "array", "items": {"type": "string"}, "description": "Declared node types."},
    "type_parents": {"type": "object", "description": "{type: [parent types]}: a type matches its subtypes' nodes."},
    "relation_parents": {"type": "object", "description": "{relation: [parent relations]}: a scope on a relation "
                         "covers its specializations."},
    "bindings": {"type": "object", "description": "{name: text}: what the projection was built from (source, schema, "
                 "policy); part of its identity."},
    "dimensions": {"type": "integer", "description": "A periodic cell's dimensions (default: its shifts' length)."},
    "periodic": {"type": "array", "items": {"type": "boolean"}, "description": "A periodic cell's repeating axes "
                 "(default: all)."},
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
        "description": "Run a request (reachability, typed_path, shortest_path, bottleneck_path, regular_path, "
                       "derivation, personalized_pagerank, dependency_impact, pattern, support, or on a periodic "
                       "cell periodicity) on a "
                       "projection: by default the one this session holds under the request's projection identity. "
                       "regular_path takes a path expression "
                       "such as 'CITES+/^SUPPORTS[Claim]' (/ then, | or, * + ? {m,n} repeats, ^ reverse, [types] "
                       "landing). Returns the result: status, answer with witnesses (and certificates when asked), "
                       "limitations, result_id. An interrupted answer never certifies a negative.",
        "input_schema": {"type": "object", "required": ["request"], "properties": {
            "request": {"type": "object", "description": "{operation, ...parameters, limits}; projection defaults to "
                                                         "the projection given here."},
            "projection": _PROJECTION,
            "timeout": {"type": "number", "description": "Seconds; an interrupted result says so."},
            "view": {"enum": ["full", "summary"], "description": "full (default): the result record; summary: its "
                     "status, counts and first items, with its result_id for graph_result."},
            "limit": {"type": "integer", "minimum": 1, "description": "Items per list in a summary (default 20)."}}},
    },
    {
        "name": "graph_result",
        "description": "A result this session holds, by result_id: whole (view full), summarized, a slice of one of "
                       "its answer's lists (field, offset, limit: e.g. reached, rows, derivation, components), or the "
                       "witness of one node of a reachability, impact or regular path reached answer (path_to).",
        "input_schema": {"type": "object", "required": ["result_id"], "properties": {
            "result_id": {"type": "string"}, "view": {"enum": ["full", "summary"]},
            "field": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1}, "path_to": {"type": "string"}}},
    },
    {
        "name": "graph_batch",
        "description": "Run several requests on one projection in one call; each comes back as its summary (or its "
                       "full result with view full), in order, a problem with one as its own error.",
        "input_schema": {"type": "object", "required": ["requests"], "properties": {
            "requests": {"type": "array", "items": {"type": "object"}}, "projection": _PROJECTION,
            "timeout": {"type": "number", "description": "Seconds, for each request."},
            "view": {"enum": ["full", "summary"]}, "limit": {"type": "integer", "minimum": 1}}},
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
        self.results: OrderedDict[str, Result] = OrderedDict()
        self.answered: dict[tuple[str, str], str] = {}        # (projection, request_id) -> result_id

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
    def _keep(self, table: OrderedDict, key: str, value: Any, keep: int = KEEP) -> Any:
        table[key] = value
        table.move_to_end(key)
        while len(table) > keep:
            table.popitem(last=False)
        return value

    def run(self, p: Any, request: Mapping, timeout: float | None) -> tuple[Result, bool]:
        """(the result of a request on a projection, whether the session already held it). A request with no deadline
        is answered once per projection: the same projection and request give the same result."""
        if not isinstance(request, Mapping):
            raise ToolError("invalid_arguments", "a request is a JSON object")
        raw = {**request, "projection": request.get("projection") or p.identity}
        key = None
        if timeout is None:
            try:
                key = (p.identity, normalize_request(raw).request_id)
            except GraphRuntimeError:
                key = None                                # a malformed request: execute says why
        if key is not None and self.answered.get(key) in self.results:
            res = self.results[self.answered[key]]
            self.results.move_to_end(res.result_id)
            return res, True
        res = execute(p, raw, timeout=timeout)
        self._keep(self.results, res.result_id, res, KEEP_RESULTS)
        if key is not None:
            self.answered[key] = res.result_id
            if len(self.answered) > 4 * KEEP_RESULTS:
                self.answered = {k: v for k, v in self.answered.items() if v in self.results}
        return res, False

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
        elif ref.get("kind") == "graph_runtime.periodic_projection":
            p = PeriodicProjection.from_dict(ref)
        else:
            stray = sorted(set(ref) - set(_RECORDS))
            if stray:
                raise ToolError("invalid_arguments", f"records take {sorted(_RECORDS)}, not {stray}")
            if "nodes" not in ref or "edges" not in ref:
                raise ToolError("invalid_arguments", "records need nodes and edges")
            periodic = "dimensions" in ref or "periodic" in ref or any(
                isinstance(e, Mapping) and "shift" in e for e in ref["edges"])
            p = PeriodicProjection.build(**ref) if periodic else Projection.build(**ref)
        return self._keep(self.projections, p.identity, p)

    # ------------------------------------------------------------------------------------------ tools
    def _graph_status(self) -> dict:
        search = importlib.util.find_spec("numpy") is not None       # without loading it
        return {"runtime": {"name": RUNTIME_NAME, "version": __version__}, "contract": CONTRACT,
                "operations": list(OPERATIONS), "text_search": search,
                "projections": [{"identity": i, "nodes": p.node_count, "edges": p.edge_count}
                                for i, p in self.projections.items()],
                "indexes": [{"index_id": i, "items": len(ix)} for i, ix in self.indexes.items()],
                "results": len(self.results), "tools": list(TOOL_NAMES)}

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
        if isinstance(p, PeriodicProjection):           # counts are the cell's; nodes are named <node>@<image>
            out.update(dimensions=p.dimensions, periodic=list(p.periodic))
        if snapshot:
            out["snapshot"] = p.to_dict()
        return out

    def _graph_execute(self, request: Mapping, projection: Mapping | None = None, timeout: float | None = None,
                       view: str = "full", limit: int = SUMMARY_ITEMS) -> dict:
        if not isinstance(request, Mapping):
            raise ToolError("invalid_arguments", "request is a JSON object")
        _view(view, limit)
        p = self.projection(projection, request.get("projection"))
        res, cached = self.run(p, request, timeout)
        return res.to_dict() if view == "full" else {**summarize(res, limit), "cached": cached}

    def _graph_result(self, result_id: str, view: str = "full", field: str | None = None, offset: int = 0,
                      limit: int | None = None, path_to: str | None = None) -> dict:
        res = self.results.get(result_id)
        if res is None:
            raise ToolError("unknown_result", f"this session holds no result {result_id!r} (it keeps the last "
                                              f"{KEEP_RESULTS}): run the request again")
        if path_to is not None:
            try:
                return {"result_id": result_id, "node": path_to, "hops": res.path_to(path_to)}
            except KeyError:
                raise ToolError("unknown_node", f"{path_to!r} is not listed in this result's answer") from None
            except ValueError as exc:
                raise ToolError("invalid_arguments", str(exc)) from None
        if field is not None:
            answer = res.to_dict(timing=False)["answer"] or {}
            items = answer.get(field)
            if not isinstance(items, list):
                lists = sorted(k for k, v in answer.items() if isinstance(v, list))
                raise ToolError("invalid_arguments", f"the answer has no list {field!r}; its lists: {lists}")
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ToolError("invalid_arguments", "offset is a non-negative integer")
            _view("full", limit if limit is not None else 1)
            end = len(items) if limit is None else offset + limit
            return {"result_id": result_id, "field": field, "offset": offset, "total": len(items),
                    "items": items[offset:end]}
        _view(view, limit if limit is not None else SUMMARY_ITEMS)
        return res.to_dict() if view == "full" else summarize(res, limit or SUMMARY_ITEMS)

    def _graph_batch(self, requests: list, projection: Mapping | None = None, timeout: float | None = None,
                     view: str = "summary", limit: int = SUMMARY_ITEMS) -> dict:
        if not isinstance(requests, list):
            raise ToolError("invalid_arguments", "requests is a list of requests")
        _view(view, limit)
        out = []
        for request in requests:
            try:
                p = self.projection(projection, request.get("projection") if isinstance(request, Mapping) else None)
                res, cached = self.run(p, request, timeout)
                out.append(res.to_dict() if view == "full" else {**summarize(res, limit), "cached": cached})
            except (ToolError, GraphRuntimeError) as e:
                out.append({"error": {"code": e.code, "message": e.message}})
        return {"results": out, "count": len(out)}

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


def _view(view: Any, limit: Any) -> None:
    if view not in ("full", "summary"):
        raise ToolError("invalid_arguments", f"view is full or summary, not {view!r}")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ToolError("invalid_arguments", "limit is a positive integer")


def summarize(res: Result, limit: int = SUMMARY_ITEMS) -> dict:
    """A result in the size a reader needs: how it ended, its counters, and what its answer says, with at most
    ``limit`` items of each list (``more`` says when there are others); nodes of a set answer nearest first (by depth,
    then ID). The full result stays in the session under its ``result_id`` (:meth:`GraphToolkit._graph_result`)."""
    d = res.to_dict(timing=False)
    out = {k: d[k] for k in ("result_id", "request_id", "projection", "operation", "status", "complete",
                             "interruption", "error", "stats")}
    a, op = d["answer"], d["operation"]
    if a is None:
        return {**out, "summary": None}

    def head(items: list, fields: tuple) -> dict:
        return {"items": [{k: x[k] for k in fields if k in x} for x in items[:limit]], "more": len(items) > limit}

    def hops(witness: dict | None) -> list | None:
        if witness is None:
            return None
        return [{k: h[k] for k in ("edge", "relation", "direction", "from", "to") if k in h} for h in
                witness["hops"][:limit]]

    def kind(cert: dict | None) -> str | None:
        return None if cert is None else cert["kind"]

    def depths(items: list, field: str) -> list:
        """[[depth, nodes at it], ...] by depth, at most ``limit`` of them."""
        tally: dict[int, int] = {}
        for e in items:
            tally[e[field]] = tally.get(e[field], 0) + 1
        return [[k, tally[k]] for k in sorted(tally)[:limit]]

    if op in ("reachability", "dependency_impact"):
        items = sorted(a["reached" if op == "reachability" else "affected"], key=lambda e: (e["depth"], e["node"]))
        summary = {"count": a["count"], "by_depth": depths(items, "depth"), **head(items, ("node", "depth"))}
    elif op == "regular_path" and "reached" in a:
        items = sorted(a["reached"], key=lambda e: (e["length"], e["node"]))
        summary = {"count": a["count"], "by_length": depths(items, "length"), **head(items, ("node", "length")),
                   "certificate": kind(a["certificate"])}
    elif op in ("typed_path", "shortest_path", "bottleneck_path", "regular_path"):
        summary = {k: a[k] for k in ("found", "optimal", "cost", "cost_exact", "lower_bound", "lower_bound_exact",
                                     "value", "bound") if k in a}
        summary.update(length=None if a["witness"] is None else a["witness"]["length"], hops=hops(a["witness"]),
                       certificate=kind(a["certificate"]))
    elif op == "pattern":
        summary = {"exists": a["exists"], "total": a["total"], "count": a["count"],
                   **head(a["rows"], ("nodes", "edges"))}
    elif op == "support":
        summary = {"counts": a["counts"]}
        for key in ("lost", "weakened"):
            if a[key] is not None:
                summary[key] = {"nodes": [e["node"] for e in a[key][:limit]], "more": len(a[key]) > limit}
    elif op == "derivation":
        summary = {"derived": a["derived"][:limit], "underived": a["underived"][:limit],
                   "more": len(a["derived"]) > limit or len(a["underived"]) > limit,
                   "derivation_nodes": len(a["derivation"]), "certificate": kind(a["certificate"]),
                   **({"bound": a["bound"]} if "bound" in a else {})}
    elif op == "personalized_pagerank":
        summary = {"count": a["count"], "certified_top": a["certified_top"], "residual": a["residual"],
                   **head(a["ranked"], ("node", "lower", "upper"))}
    elif op == "periodicity":
        summary = {"count": a["count"], "components": [
            {"seeds": c["seeds"][:limit], "rank": c["rank"], "basis": c["basis"], "copies": c["copies"],
             "nodes": len(c["nodes"])} for c in a["components"][:limit]], "more": a["count"] > limit}
    else:
        summary = {"keys": sorted(a)}
    return {**out, "summary": summary,
            "full": "graph_result with this result_id gives the whole answer, a slice of one of its lists (field, "
                    "offset, limit) or one node's witness (path_to)"}
