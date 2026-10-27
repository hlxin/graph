"""Requests: what a consumer asks, normalized into one canonical form that the result echoes and identifies.

A request names its operation, the identity of the projection it was made for, its explicit scope and its limits.
Normalization checks its shape, fills every default explicitly, sorts and de-duplicates its sets, and gives it an
identity (``request_id``, a digest of the normalized form): two requests that ask the same thing normalize to the
same form and the same identity. Whether the nodes, relations, types and edges it names exist is checked against
the projection at execution (:func:`graph_runtime.execute`), after the projection identity itself.

Operations (docs/API_CONTRACT.md):

* ``reachability``: ``sources``, ``scope``, ``max_depth`` (null: unbounded); limits ``max_expansions``,
  ``max_results``.
* ``typed_path``: ``sources``, ``pattern`` (positions ``{scope, node_types}``), ``targets`` (null: any node),
  ``certificate`` (false: no proof of a negative); limit ``max_expansions``.
* ``shortest_path``: ``sources``, ``targets``, ``scope``, ``costs`` (``{"by_relation": {...}}`` or
  ``{"by_edge": {...}}``), ``certificate`` (false: no proof of optimality or of a negative); limit
  ``max_expansions``.
* ``dependency_impact``: ``changed``, ``scope``, ``max_depth``; limits ``max_expansions``, ``max_results``.
* ``pattern``: ``match`` (``{var, node_types, nodes}``), ``steps``, ``answer`` (``rows``, ``count`` or
  ``exists``); limits ``max_expansions``, ``max_results``.
* ``support``: ``failed``, ``scope`` (from a node to what supports it), ``all_of`` (node types that need every
  support; null: every node needs one), ``certificate``; limits ``max_expansions``, ``max_results``.
* ``bottleneck_path``: ``sources``, ``targets``, ``scope``, ``weights`` (as costs, any finite number),
  ``objective`` (``max_min``: the path whose weakest edge is strongest; ``min_max``: the path whose highest edge is
  lowest), ``certificate``; limit ``max_expansions``.
* ``regular_path``: ``sources``, ``path`` (a regular path expression: text, a list or a tree, see
  :mod:`graph_runtime.paths`), ``targets`` (null: any node), ``answer`` (``witness``: one walk with the fewest hops;
  ``reached``: every node a matching walk reaches), ``certificate``; limits ``max_expansions``, ``max_results``.
* ``periodicity`` (periodic projections only): ``nodes`` (images), ``scope`` (followed both ways); limit
  ``max_expansions``.
* ``personalized_pagerank``: ``sources``, ``scope`` (the hops a walk follows), ``alpha`` (the probability of stopping
  at each step; default ``3/20``), ``tolerance`` (the residual to reach; default ``1/10000``), ``top`` (nodes listed;
  default 20), ``certificate``; limit ``max_expansions``.
* ``derivation``: ``targets``, ``scope`` (from a node to what it needs), ``all_of`` (node types that need every input;
  null: every node needs one), ``grounds`` (nodes given; null: every node with no input), ``costs`` (as a shortest
  path's; null: every hop costs 1), ``objective`` (``min_sum``: the least total cost; ``min_max``: the least highest
  cost; ``max_min``: the greatest lowest), ``certificate``; limit ``max_expansions``.
* ``components``: ``scope``, ``connectivity`` (``weak``: hops followed either way; ``strong``: walks each way), ``nodes``
  (only the components holding these; null: every component), ``min_size`` (default 1), ``certificate``; limits
  ``max_expansions``, ``max_results`` (components listed).
* ``biconnectivity``: ``scope`` (each edge in it a link between its ends, either way), ``nodes`` (only the
  components holding these; null: all), ``blocks`` (list the blocks too; default false), ``certificate``; limit
  ``max_expansions``.
* ``max_flow``: ``sources``, ``targets`` (disjoint from the sources), ``scope``, ``capacities`` (as costs; null: every
  edge 1), ``node_capacity`` (what each node other than the sources and targets passes; null: no limit),
  ``certificate``; limit ``max_expansions``.
* ``matching``: ``left`` (``{node_types, nodes}``: the nodes to pair), ``scope`` (from a left node to its candidate
  partners), ``certificate``; limit ``max_expansions``.
* ``dominators``: ``sources``, ``scope``, ``targets`` (null: every node reached, with its immediate dominator; else
  each target's dominators), ``certificate``; limit ``max_expansions``.
* ``critical_path``: ``scope`` (a hop from u to v: u comes first), ``sources`` (only what they reach; null: every
  node), ``costs`` (as a shortest path's; null: every hop takes 1), ``answer`` (``path``: the makespan and a critical
  path; ``schedule``: every node's earliest and latest time and slack too), ``certificate``; limit
  ``max_expansions``.
* ``d_separation``: ``x``, ``y``, ``given`` (default none; the three disjoint), ``scope`` (a hop from u to v: u is a
  parent of v), ``certificate``; limit ``max_expansions``.
* ``incidence``: ``rows``, ``columns`` (``{node_types, nodes}`` each, disjoint), ``scope``, ``coefficients`` (as
  weights: any finite numbers, by relation or by edge), ``kernels`` (a subset of ``left`` and ``right``; default
  none), ``certificate``; limit ``max_expansions``.
* ``spanning_forest``: ``scope`` (each edge in it a link between its ends, either way), ``weights`` (any finite numbers,
  by relation or by edge; null: every link weighs 1), ``objective`` (``min``, the default, or ``max``); limit
  ``max_expansions``.
* ``cycle_basis``: ``scope`` (each edge in it a link between its ends, either way), ``weights`` (as costs: non-negative;
  null: every link weighs 1), ``certificate``; limit ``max_expansions``.

No operation takes a field named ``kind``: a request's identity digests its fields with ``kind`` naming the record
kind (``graph_runtime.request``).

A scope is a list of ``{relation, direction}`` (``forward``: from an edge's source to its target; ``reverse``: from
its target to its source); there is no wildcard.
"""

from __future__ import annotations

import math
import numbers
import re
import sys
from collections.abc import Mapping
from typing import Any

from . import paths
from ._canonical import canonical_json, canonical_object, freeze, is_digest, sha256_text, thaw
from .errors import RequestError
from .projection import CONTRACT, DIRECTIONS

OPERATIONS = ("reachability", "typed_path", "shortest_path", "dependency_impact", "pattern", "support",
              "bottleneck_path", "regular_path", "derivation", "periodicity", "personalized_pagerank", "components",
              "biconnectivity", "max_flow", "matching", "dominators", "incidence", "critical_path", "d_separation",
              "spanning_forest", "cycle_basis")
FOREST_OBJECTIVES = ("min", "max")
OBJECTIVES = ("max_min", "min_max")
PATTERN_ANSWERS = ("rows", "count", "exists")
PATH_ANSWERS = ("witness", "reached")
DERIVATION_OBJECTIVES = ("min_sum", "min_max", "max_min")
CONNECTIVITIES = ("weak", "strong")
MODES = ("exact",)
FAILED, UNSUPPORTED = "failed", "unsupported"
REQUEST_KIND = "graph_runtime.request"

DEFAULT_LIMITS = {"max_expansions": 1_000_000, "max_results": 100_000}
_LIMITS = {"reachability": ("max_expansions", "max_results"),
           "dependency_impact": ("max_expansions", "max_results"),
           "typed_path": ("max_expansions",),
           "shortest_path": ("max_expansions",),
           "pattern": ("max_expansions", "max_results"),
           "support": ("max_expansions", "max_results"),
           "bottleneck_path": ("max_expansions",),
           "regular_path": ("max_expansions", "max_results"),
           "derivation": ("max_expansions",),
           "periodicity": ("max_expansions",),
           "personalized_pagerank": ("max_expansions",),
           "components": ("max_expansions", "max_results"),
           "biconnectivity": ("max_expansions",),
           "max_flow": ("max_expansions",),
           "matching": ("max_expansions",),
           "dominators": ("max_expansions",),
           "incidence": ("max_expansions",),
           "critical_path": ("max_expansions",),
           "d_separation": ("max_expansions",),
           "spanning_forest": ("max_expansions",),
           "cycle_basis": ("max_expansions",)}
_PARAMS = {"reachability": (("sources", "scope"), ("max_depth",)),
           "dependency_impact": (("changed", "scope"), ("max_depth",)),
           "typed_path": (("sources", "pattern"), ("targets", "certificate")),
           "shortest_path": (("sources", "targets", "scope", "costs"), ("certificate",)),
           "pattern": (("match",), ("steps", "answer", "injective")),
           "support": (("failed", "scope"), ("all_of", "certificate")),
           "bottleneck_path": (("sources", "targets", "scope", "weights"), ("objective", "certificate")),
           "regular_path": (("sources", "path"), ("targets", "answer", "certificate")),
           "derivation": (("targets", "scope"), ("all_of", "grounds", "costs", "objective", "certificate")),
           "periodicity": (("nodes", "scope"), ()),
           "personalized_pagerank": (("sources", "scope"), ("alpha", "tolerance", "top", "certificate")),
           "components": (("scope",), ("connectivity", "nodes", "min_size", "certificate")),
           "biconnectivity": (("scope",), ("nodes", "blocks", "certificate")),
           "max_flow": (("sources", "targets", "scope"), ("capacities", "node_capacity", "certificate")),
           "matching": (("left", "scope"), ("certificate",)),
           "dominators": (("sources", "scope"), ("targets", "certificate")),
           "incidence": (("rows", "columns", "scope", "coefficients"), ("kernels", "certificate")),
           "critical_path": (("scope",), ("sources", "costs", "answer", "certificate")),
           "d_separation": (("x", "y", "scope"), ("given", "certificate")),
           "spanning_forest": (("scope",), ("weights", "objective")),
           "cycle_basis": (("scope",), ("weights", "certificate"))}
_COMMON = ("contract", "operation", "mode", "projection", "limits")


def _fail(code: str, message: str, **detail: Any) -> RequestError:
    return RequestError(FAILED, code, message, detail or None)


class Request:
    """A normalized request: read-only, with its identity. Make one with :func:`normalize_request` or
    :meth:`graph_runtime.Projection.request`."""

    __slots__ = ("_data", "_id", "_json")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("make a Request with normalize_request(...) or Projection.request(...)")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a Request is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("a Request is immutable")

    @classmethod
    def _make(cls, data: dict) -> "Request":
        """Freeze the normalized data, and encode each field once: the request's canonical JSON (which a result
        echoes) and its identity (the digest of the same fields with the request kind) share the encodings."""
        r = object.__new__(cls)
        parts = {k: canonical_json(v) for k, v in data.items()}
        text = canonical_object(parts)
        object.__setattr__(r, "_id", sha256_text(canonical_object({**parts, "kind": canonical_json(REQUEST_KIND)})))
        object.__setattr__(r, "_json", text)
        object.__setattr__(r, "_data", freeze(data))
        return r

    @property
    def operation(self) -> str:
        return self._data["operation"]

    @property
    def projection(self) -> str:
        return self._data["projection"]

    @property
    def request_id(self) -> str:
        return self._id

    @property
    def limits(self) -> Mapping[str, int]:
        return self._data["limits"]

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def to_dict(self) -> dict:
        return thaw(self._data)

    def __repr__(self) -> str:
        return f"<Request {self.operation} {self._id[:19]}…>"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Request) and other._id == self._id

    def __hash__(self) -> int:
        return hash(self._id)


def normalize_request(raw: Mapping | Request) -> Request:
    """Normalize a request (a mapping, as JSON gives it). Raises :class:`RequestError` with status ``failed`` for a
    malformed request and ``unsupported`` for one the v0 contract does not provide (another operation, a mode other
    than ``exact``, another contract version, negative costs)."""
    try:
        return _normalize_request(raw)
    except (ValueError, TypeError, OverflowError) as exc:
        raise _fail("invalid_request", f"a request must be representable as JSON: {exc}") from None


def _normalize_request(raw: Mapping | Request) -> Request:
    if isinstance(raw, Request):
        return raw
    if not isinstance(raw, Mapping):
        raise _fail("invalid_request", f"a request is a mapping, not {type(raw).__name__}")
    contract = raw.get("contract", CONTRACT)
    if contract != CONTRACT:
        raise RequestError(UNSUPPORTED, "unsupported_contract", f"this runtime implements {CONTRACT!r}, not "
                           f"{contract!r}", {"contract": contract})
    op = raw.get("operation")
    if not isinstance(op, str) or not op:
        raise _fail("invalid_request", "a request names its operation")
    if op not in OPERATIONS:
        raise RequestError(UNSUPPORTED, "unsupported_operation", f"the operation {op!r} is not part of {CONTRACT} "
                           f"(operations: {', '.join(OPERATIONS)})", {"operation": op})
    mode = raw.get("mode", "exact")
    if mode not in MODES:
        raise RequestError(UNSUPPORTED, "unsupported_mode", f"{CONTRACT} is exact-only, not {mode!r}", {"mode": mode})
    required, optional = _PARAMS[op]
    unknown = sorted(str(k) for k in raw if k not in _COMMON and k not in required and k not in optional)
    if unknown:
        raise _fail("invalid_request", f"{op} does not take {unknown}", fields=unknown)
    missing = [k for k in required if raw.get(k) is None]
    if missing:
        raise _fail("invalid_request", f"{op} needs {missing}", fields=missing)
    projection = raw.get("projection")
    if not is_digest(projection):
        raise _fail("invalid_request", "a request names the identity of the projection it was made for "
                    "(projection: sha256:<64 hex>)")

    data: dict[str, Any] = {"contract": CONTRACT, "operation": op, "mode": mode, "projection": projection,
                            "limits": _limits(raw.get("limits"), op)}
    if op in ("reachability", "dependency_impact"):
        data["changed" if op == "dependency_impact" else "sources"] = _ids(
            raw["changed" if op == "dependency_impact" else "sources"],
            "changed" if op == "dependency_impact" else "sources")
        data["scope"] = _scope(raw["scope"], "scope")
        data["max_depth"] = _depth(raw.get("max_depth"))
    elif op == "typed_path":
        data["sources"] = _ids(raw["sources"], "sources")
        data["targets"] = None if raw.get("targets") is None else _ids(raw["targets"], "targets")
        data["pattern"] = _pattern(raw["pattern"])
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "pattern":
        data["match"], data["steps"] = _graph_pattern(raw["match"], raw.get("steps") or [])
        data["answer"] = raw.get("answer", "rows")
        if data["answer"] not in PATTERN_ANSWERS:
            raise _fail("invalid_request", f"answer is one of {list(PATTERN_ANSWERS)}, not {data['answer']!r}")
        data["injective"] = _flag(raw.get("injective", False), "injective")
        if data["injective"] and any(st.get("optional") for st in data["steps"]):
            raise RequestError(UNSUPPORTED, "unsupported_mode", f"{CONTRACT} matches injective patterns without "
                               "optional steps (which node an optional step binds, among the distinct ones left, is "
                               "not yet defined)")
    elif op == "bottleneck_path":
        data["sources"] = _ids(raw["sources"], "sources")
        data["targets"] = _ids(raw["targets"], "targets")
        data["scope"] = _scope(raw["scope"], "scope")
        data["weights"] = _weights(raw["weights"])
        data["objective"] = raw.get("objective", "max_min")
        if data["objective"] not in OBJECTIVES:
            raise _fail("invalid_request", f"objective is one of {list(OBJECTIVES)}, not {data['objective']!r}")
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "regular_path":
        data["sources"] = _ids(raw["sources"], "sources")
        data["targets"] = None if raw.get("targets") is None else _ids(raw["targets"], "targets")
        try:
            data["path"] = paths.normalize(raw["path"], _scope, _type_list)
        except paths.PathError as exc:
            raise _fail("invalid_request", f"path: {exc}") from None
        data["answer"] = raw.get("answer", "witness")
        if data["answer"] not in PATH_ANSWERS:
            raise _fail("invalid_request", f"answer is one of {list(PATH_ANSWERS)}, not {data['answer']!r}")
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "derivation":
        data["targets"] = _ids(raw["targets"], "targets")
        data["scope"] = _scope(raw["scope"], "scope")
        data["all_of"] = _node_constraint(raw, "derivation", ("all_of",))["all_of"]
        data["grounds"] = None if raw.get("grounds") is None else _ids(raw["grounds"], "grounds")
        data["objective"] = raw.get("objective", "min_sum")
        if data["objective"] not in DERIVATION_OBJECTIVES:
            raise _fail("invalid_request", f"objective is one of {list(DERIVATION_OBJECTIVES)}, not "
                        f"{data['objective']!r}")
        costs = raw.get("costs")
        data["costs"] = (None if costs is None else _costs(costs) if data["objective"] == "min_sum"
                         else _weights(costs, "costs"))
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "personalized_pagerank":
        data["sources"] = _ids(raw["sources"], "sources")
        data["scope"] = _scope(raw["scope"], "scope")
        data["alpha"] = _fraction(raw.get("alpha", "3/20"), "alpha", 0, 1)
        data["tolerance"] = _fraction(raw.get("tolerance", "1/10000"), "tolerance", 0, 1)
        top = raw.get("top", 20)
        if isinstance(top, bool) or not isinstance(top, int) or top < 1:
            raise _fail("invalid_request", f"top is a positive integer, not {top!r}")
        data["top"] = top
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "components":
        data["scope"] = _scope(raw["scope"], "scope")
        data["connectivity"] = raw.get("connectivity", "weak")
        if data["connectivity"] not in CONNECTIVITIES:
            raise _fail("invalid_request", f"connectivity is one of {list(CONNECTIVITIES)}, not "
                        f"{data['connectivity']!r}")
        data["nodes"] = None if raw.get("nodes") is None else _ids(raw["nodes"], "nodes")
        size = raw.get("min_size", 1)
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise _fail("invalid_request", f"min_size is a positive integer, not {size!r}")
        data["min_size"] = size
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "biconnectivity":
        data["scope"] = _scope(raw["scope"], "scope")
        data["nodes"] = None if raw.get("nodes") is None else _ids(raw["nodes"], "nodes")
        data["blocks"] = _flag(raw.get("blocks", False), "blocks")
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "max_flow":
        data["sources"] = _ids(raw["sources"], "sources")
        data["targets"] = _ids(raw["targets"], "targets")
        both = sorted(set(data["sources"]) & set(data["targets"]))
        if both:
            raise _fail("invalid_request", f"sources and targets are disjoint; both name {both[:10]}")
        data["scope"] = _scope(raw["scope"], "scope")
        data["capacities"] = None if raw.get("capacities") is None else _capacities(raw["capacities"])
        cap = raw.get("node_capacity")
        if cap is not None:
            cap = _capacity(cap, "node_capacity")
        data["node_capacity"] = cap
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "matching":
        left = raw["left"]
        if not isinstance(left, Mapping) or any(k not in ("node_types", "nodes") for k in left):
            raise _fail("invalid_request", "left is {node_types, nodes}: the nodes to pair")
        data["left"] = _node_constraint(left, "left")
        if data["left"]["node_types"] is None and data["left"]["nodes"] is None:
            raise _fail("invalid_request", "left names node_types or nodes")
        data["scope"] = _scope(raw["scope"], "scope")
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "dominators":
        data["sources"] = _ids(raw["sources"], "sources")
        data["scope"] = _scope(raw["scope"], "scope")
        data["targets"] = None if raw.get("targets") is None else _ids(raw["targets"], "targets")
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "d_separation":
        data["x"], data["y"] = _ids(raw["x"], "x"), _ids(raw["y"], "y")
        given = raw.get("given")
        data["given"] = [] if given is None or (isinstance(given, (list, tuple)) and not given) else _ids(given, "given")
        overlap = (set(data["x"]) & set(data["y"])) | ((set(data["x"]) | set(data["y"])) & set(data["given"]))
        if overlap:
            raise _fail("invalid_request", f"x, y and given are disjoint; {sorted(overlap)[:10]} is in two of them")
        data["scope"] = _scope(raw["scope"], "scope")
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "cycle_basis":
        data["scope"] = _scope(raw["scope"], "scope")
        data["weights"] = None if raw.get("weights") is None else _costs(raw["weights"])
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "spanning_forest":
        data["scope"] = _scope(raw["scope"], "scope")
        data["weights"] = None if raw.get("weights") is None else _weights(raw["weights"])
        data["objective"] = raw.get("objective", "min")
        if data["objective"] not in FOREST_OBJECTIVES:
            raise _fail("invalid_request", f"objective is one of {list(FOREST_OBJECTIVES)}, not {data['objective']!r}")
    elif op == "critical_path":
        data["scope"] = _scope(raw["scope"], "scope")
        data["sources"] = None if raw.get("sources") is None else _ids(raw["sources"], "sources")
        data["costs"] = None if raw.get("costs") is None else _costs(raw["costs"])
        data["answer"] = raw.get("answer", "path")
        if data["answer"] not in ("path", "schedule"):
            raise _fail("invalid_request", f"answer is path or schedule, not {data['answer']!r}")
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "incidence":
        for field in ("rows", "columns"):
            spec = raw[field]
            if not isinstance(spec, Mapping) or any(k not in ("node_types", "nodes") for k in spec):
                raise _fail("invalid_request", f"{field} is {{node_types, nodes}}")
            data[field] = _node_constraint(spec, field)
            if data[field]["node_types"] is None and data[field]["nodes"] is None:
                raise _fail("invalid_request", f"{field} names node_types or nodes")
        data["scope"] = _scope(raw["scope"], "scope")
        data["coefficients"] = _weights(raw["coefficients"], "coefficients")
        kernels = raw.get("kernels", [])
        if isinstance(kernels, (str, bytes)) or not isinstance(kernels, (list, tuple)) or any(
                k not in ("left", "right") for k in kernels):
            raise _fail("invalid_request", f"kernels is a list of left and right, not {kernels!r}")
        data["kernels"] = sorted(set(kernels))
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    elif op == "periodicity":
        data["nodes"] = _ids(raw["nodes"], "nodes")
        data["scope"] = _scope(raw["scope"], "scope")
    elif op == "support":
        data["failed"] = _ids(raw["failed"], "failed")
        data["scope"] = _scope(raw["scope"], "scope")
        data["all_of"] = _node_constraint(raw, "support", ("all_of",))["all_of"]
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    else:
        data["sources"] = _ids(raw["sources"], "sources")
        data["targets"] = _ids(raw["targets"], "targets")
        data["scope"] = _scope(raw["scope"], "scope")
        data["costs"] = _costs(raw["costs"], signed=True)
        data["certificate"] = _flag(raw.get("certificate", False), "certificate")
    try:
        return Request._make(data)
    except UnicodeEncodeError:
        raise _fail("invalid_request", "a request's text is valid Unicode (it holds an unpaired surrogate)") from None
    except ValueError as exc:            # an integer too long for JSON (Python's integer string limit)
        raise _fail("invalid_request", f"a request holds a number JSON cannot write: {exc}") from None


def _limits(value: Any, op: str) -> dict[str, int]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise _fail("invalid_request", "limits is a mapping of limit names to non-negative integers")
    allowed = _LIMITS[op]
    stray = sorted(str(k) for k in value if k not in allowed)
    if stray:
        raise _fail("invalid_request", f"{op} takes the limits {list(allowed)}, not {stray}", fields=stray)
    out = {}
    for name in allowed:
        v = value.get(name, DEFAULT_LIMITS[name])
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise _fail("invalid_request", f"limits.{name} must be a non-negative integer, not {v!r}")
        out[name] = v
    return out


def _ids(value: Any, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise _fail("invalid_request", f"{field} is a list of node IDs, not {value!r}")
    out = set()
    for v in value:
        if not isinstance(v, str) or not v:
            raise _fail("invalid_request", f"{field} holds {v!r}; a node ID is a non-empty string")
        out.add(v)
    if not out:
        raise _fail("invalid_request", f"{field} names no node")
    return sorted(out)


def _scope(value: Any, field: str) -> list[dict[str, str]]:
    """``[{relation, direction}]`` (a pair ``[relation, direction]`` is read as one), sorted and de-duplicated."""
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, (list, tuple)):
        raise _fail("invalid_request", f"{field} is a list of {{relation, direction}}, not {value!r}")
    seen = set()
    for entry in value:
        if isinstance(entry, Mapping):
            stray = sorted(str(k) for k in entry if k not in ("relation", "direction"))
            if stray:
                raise _fail("invalid_request", f"{field} entries take relation and direction, not {stray}")
            rel, direction = entry.get("relation"), entry.get("direction")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            rel, direction = entry
        else:
            raise _fail("invalid_request", f"{field} holds {entry!r}; an entry is {{relation, direction}}")
        if not isinstance(rel, str) or not rel:
            raise _fail("invalid_request", f"{field} holds the relation {rel!r}; a relation is a non-empty string")
        if direction not in DIRECTIONS:
            raise _fail("invalid_request", f"{field}: the direction of {rel} is {direction!r}, not one of "
                        f"{list(DIRECTIONS)}")
        seen.add((rel, direction))
    if not seen:
        raise _fail("invalid_request", f"{field} selects no relation: a scope is explicit, there is no wildcard")
    return [{"relation": r, "direction": d} for r, d in sorted(seen)]


def _fraction(value: Any, field: str, low: int, high: int) -> str:
    """A probability strictly between ``low`` and ``high``, given as a number (a float is read as its exact binary
    value) or as text ``p/q``, normalized to ``p/q`` (or ``p``) in lowest terms."""
    from fractions import Fraction
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError
        x = Fraction(value)
    except (ValueError, ZeroDivisionError, OverflowError):
        raise _fail("invalid_request", f"{field} is a number or a fraction p/q, not {value!r}") from None
    if not low < x < high:
        raise _fail("invalid_request", f"{field} lies strictly between {low} and {high}, not {value!r}")
    return str(x)


def _flag(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise _fail("invalid_request", f"{field} is true or false, not {value!r}")
    return value


def _depth(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("invalid_request", f"max_depth is a non-negative integer or null, not {value!r}")
    return value


def _pattern(value: Any) -> list[dict]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, (list, tuple)) or not value:
        raise _fail("invalid_request", "pattern is a non-empty list of positions {scope, node_types}")
    out = []
    for i, pos in enumerate(value):
        if not isinstance(pos, Mapping):
            raise _fail("invalid_request", f"pattern[{i}] is a mapping {{scope, node_types}}, not {pos!r}")
        stray = sorted(str(k) for k in pos if k not in ("scope", "node_types"))
        if stray:
            raise _fail("invalid_request", f"pattern[{i}] takes scope and node_types, not {stray}")
        if pos.get("scope") is None:
            raise _fail("invalid_request", f"pattern[{i}] needs a scope")
        types = pos.get("node_types")
        if types is not None:
            if isinstance(types, (str, bytes)) or not isinstance(types, (list, tuple)) or not types:
                raise _fail("invalid_request", f"pattern[{i}].node_types is a non-empty list of types, or null")
            if not all(isinstance(t, str) and t for t in types):
                raise _fail("invalid_request", f"pattern[{i}].node_types holds a type that is not a non-empty string")
            types = sorted(set(types))
        out.append({"scope": _scope(pos["scope"], f"pattern[{i}].scope"), "node_types": types})
    return out


def _costs(value: Any, signed: bool = False) -> dict[str, dict[str, int | float]]:
    """``{"by_relation": {...}}`` or ``{"by_edge": {...}}`` of costs: integers of any size or finite floats, never
    negative unless ``signed`` (a shortest path's costs, which may be)."""
    if not isinstance(value, Mapping) or len(value) != 1 or next(iter(value)) not in ("by_relation", "by_edge"):
        raise _fail("invalid_request", 'costs is {"by_relation": {relation: cost}} or {"by_edge": {edge: cost}}')
    kind, table = next(iter(value.items()))
    if not isinstance(table, Mapping):
        raise _fail("invalid_request", f"costs.{kind} is a mapping of IDs to costs")
    out = {}
    for key, x in table.items():
        if type(key) is not str or not key:
            raise _fail("invalid_request", f"costs.{kind} holds the key {key!r}; a key is a non-empty string")
        if type(x) is int and (x >= 0 or signed) or type(x) is float and (0 if not signed else -_FLOAT_MAX) <= x \
                <= _FLOAT_MAX:
            out[key] = x                       # the common case, checked without a call
        else:
            out[key] = _cost(x, kind, key, signed)
    return {kind: out}                         # canonical JSON sorts the keys; nothing else reads their order


_FLOAT_MAX = sys.float_info.max


def _weights(value: Any, field: str = "weights") -> dict[str, dict[str, int | float]]:
    """``{"by_relation": {...}}`` or ``{"by_edge": {...}}`` of finite numbers of any sign: a bottleneck compares
    weights and never adds them, so any finite number is exact (as are a min_max or max_min derivation's costs)."""
    if not isinstance(value, Mapping) or len(value) != 1 or next(iter(value)) not in ("by_relation", "by_edge"):
        raise _fail("invalid_request", f'{field} is {{"by_relation": {{relation: weight}}}} or {{"by_edge": {{edge: '
                    'weight}}')
    kind, table = next(iter(value.items()))
    if not isinstance(table, Mapping):
        raise _fail("invalid_request", f"{field}.{kind} is a mapping of IDs to weights")
    out = {}
    for key, x in table.items():
        if type(key) is not str or not key:
            raise _fail("invalid_request", f"{field}.{kind} holds the key {key!r}; a key is a non-empty string")
        if isinstance(x, bool) or not isinstance(x, (numbers.Integral, float)):
            raise _fail("invalid_cost", f"{field}.{kind}[{key!r}] is {x!r}; a weight is an integer or a finite float")
        x = int(x) if isinstance(x, numbers.Integral) else float(x)
        if isinstance(x, float) and not math.isfinite(x):
            raise _fail("non_finite_cost", f"{field}.{kind}[{key!r}] is {x!r}; weights are finite")
        out[key] = x
    return {kind: out}


def _capacities(value: Any) -> dict[str, dict[str, int | float]]:
    """``{"by_relation": {...}}`` or ``{"by_edge": {...}}`` of capacities: non-negative integers or finite floats."""
    if not isinstance(value, Mapping) or len(value) != 1 or next(iter(value)) not in ("by_relation", "by_edge"):
        raise _fail("invalid_request", 'capacities is {"by_relation": {relation: capacity}} or {"by_edge": {edge: '
                    'capacity}}')
    kind, table = next(iter(value.items()))
    if not isinstance(table, Mapping):
        raise _fail("invalid_request", f"capacities.{kind} is a mapping of IDs to capacities")
    out = {}
    for key, x in table.items():
        if type(key) is not str or not key:
            raise _fail("invalid_request", f"capacities.{kind} holds the key {key!r}; a key is a non-empty string")
        out[key] = _capacity(x, f"capacities.{kind}[{key!r}]")
    return {kind: out}


def _capacity(x: Any, where: str) -> int | float:
    """A capacity: a non-negative integer (any size, kept exact) or a non-negative finite float."""
    if isinstance(x, bool) or not isinstance(x, (numbers.Integral, float)):
        raise _fail("invalid_cost", f"{where} is {x!r}; a capacity is an integer or a finite float")
    x = int(x) if isinstance(x, numbers.Integral) else float(x)
    if isinstance(x, float) and not math.isfinite(x) or x < 0:
        raise _fail("invalid_cost", f"{where} is {x!r}; a capacity is finite and not negative")
    return x


def _cost(x: Any, kind: str, key: str, signed: bool = False) -> int | float:
    """A cost: an integer (any size, kept exact) or a finite float, never negative unless ``signed``."""
    where = f"costs.{kind}[{key!r}]"
    if isinstance(x, bool) or not isinstance(x, (numbers.Integral, float)):
        raise _fail("invalid_cost", f"{where} is {x!r}; a cost is an integer or a finite float")
    x = int(x) if isinstance(x, numbers.Integral) else float(x)
    if isinstance(x, float) and not math.isfinite(x):
        raise _fail("non_finite_cost", f"{where} is {x!r}; {CONTRACT} accepts finite costs only")
    if x < 0 and not signed:
        raise RequestError(UNSUPPORTED, "negative_cost", f"{where} is {x!r}; {CONTRACT} supports non-negative "
                           "additive costs only", {"where": where})
    return x


_VAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _var(value: Any, where: str, taken: set) -> str:
    if not isinstance(value, str) or not _VAR.match(value):
        raise _fail("invalid_request", f"{where} is a variable name (a letter or _, then letters, digits or _), "
                    f"not {value!r}")
    if value in taken:
        raise _fail("invalid_request", f"{where}: the variable {value!r} is already bound")
    taken.add(value)
    return value


def _type_list(value: Any, where: str) -> list[str]:
    """A non-empty list of node types, sorted and de-duplicated (a path step's node_types)."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or not value:
        raise _fail("invalid_request", f"{where} is a non-empty list of node types, or null")
    if not all(isinstance(t, str) and t for t in value):
        raise _fail("invalid_request", f"{where} holds a type that is not a non-empty string")
    return sorted(set(value))


def _node_constraint(raw: Mapping, where: str, fields: tuple[str, ...] = ("node_types", "nodes")) -> dict:
    out = {}
    for field in fields:
        v = raw.get(field)
        if v is not None:
            if isinstance(v, (str, bytes)) or not isinstance(v, (list, tuple)) or not v:
                raise _fail("invalid_request", f"{where}.{field} is a non-empty list, or null")
            if not all(isinstance(x, str) and x for x in v):
                raise _fail("invalid_request", f"{where}.{field} holds a value that is not a non-empty string")
            v = sorted(set(v))
        out[field] = v
    return out


def _graph_pattern(match: Any, steps: Any) -> tuple[dict, list[dict]]:
    """A pattern: a matched variable, then steps, each from a variable bound before it to a new one. A step from a
    variable an optional step binds is optional too, so that an unmatched optional part never removes a row. A step
    with ``to`` instead of ``var`` closes a cycle: it binds no variable, and needs a hop from one variable bound before
    it to another (neither bound by an optional step); its normal form is ``{from, to, scope, edges, edge_var}``."""
    if not isinstance(match, Mapping):
        raise _fail("invalid_request", "match is {var, node_types, nodes}")
    stray = sorted(str(k) for k in match if k not in ("var", "node_types", "nodes"))
    if stray:
        raise _fail("invalid_request", f"match takes var, node_types and nodes, not {stray}")
    taken: set[str] = set()
    m = {"var": _var(match.get("var"), "match.var", taken), **_node_constraint(match, "match")}
    if m["node_types"] is None and m["nodes"] is None:
        raise _fail("invalid_request", "match names node_types or nodes: a pattern starts from chosen records")
    if isinstance(steps, (str, bytes, Mapping)) or not isinstance(steps, (list, tuple)):
        raise _fail("invalid_request", "steps is a list of steps")
    optional_vars: set[str] = set()
    out = []
    for i, st in enumerate(steps):
        where = f"steps[{i}]"
        if not isinstance(st, Mapping):
            raise _fail("invalid_request", f"{where} is a mapping")
        if "to" in st:
            out.append(_closing_step(st, where, taken, optional_vars, {x.get("edge_var") for x in out}))
            continue
        stray = sorted(str(k) for k in st if k not in ("from", "var", "scope", "node_types", "nodes", "edges",
                                                         "edge_var", "optional"))
        if stray:
            raise _fail("invalid_request", f"{where} takes from, var, scope, node_types, nodes, edges, edge_var and "
                        f"optional, not {stray}")
        src = st.get("from")
        if src not in taken or src in {x.get("edge_var") for x in out}:
            raise _fail("invalid_request", f"{where}.from names {src!r}, which no earlier step or match binds to a node")
        optional = st.get("optional", False)
        if not isinstance(optional, bool):
            raise _fail("invalid_request", f"{where}.optional is true or false")
        if src in optional_vars and not optional:
            raise _fail("invalid_request", f"{where} starts from {src!r}, which an optional step binds: it is optional "
                        "too (an unmatched optional part never removes a row)")
        if st.get("scope") is None:
            raise _fail("invalid_request", f"{where} needs a scope")
        step = {"from": src, "var": None, "scope": _scope(st["scope"], f"{where}.scope"),
                **_node_constraint(st, where), "edges": None, "edge_var": None, "optional": optional}
        step["var"] = _var(st.get("var"), f"{where}.var", taken)
        if st.get("edges") is not None:
            step["edges"] = _ids(st["edges"], f"{where}.edges")
        if st.get("edge_var") is not None:
            step["edge_var"] = _var(st["edge_var"], f"{where}.edge_var", taken)
        if optional:
            optional_vars.add(step["var"])
        out.append(step)
    return m, out


def _closing_step(st: Mapping, where: str, taken: set, optional_vars: set, edge_vars: set) -> dict:
    """A step that closes a cycle: a hop in its scope (through its edges, when it names them) from the node one bound
    variable holds to the node another (or the same) holds."""
    stray = sorted(str(k) for k in st if k not in ("from", "to", "scope", "edges", "edge_var", "optional",
                                                     "node_types", "nodes"))
    if stray:
        raise _fail("invalid_request", f"{where} closes a cycle: it takes from, to, scope, edges and edge_var, not "
                    f"{stray}")
    if st.get("optional", False) is not False or st.get("node_types") is not None or st.get("nodes") is not None:
        raise _fail("invalid_request", f"{where} closes a cycle between bound variables: it is never optional and "
                    "constrains no node")
    for field in ("from", "to"):
        name = st.get(field)
        if name not in taken or name in edge_vars:
            raise _fail("invalid_request", f"{where}.{field} names {name!r}, which no earlier step or match binds to "
                        "a node")
        if name in optional_vars:
            raise _fail("invalid_request", f"{where}.{field} names {name!r}, which an optional step binds: a cycle "
                        "closes between required variables")
    if st.get("scope") is None:
        raise _fail("invalid_request", f"{where} needs a scope")
    step = {"from": st["from"], "to": st["to"], "scope": _scope(st["scope"], f"{where}.scope"), "edges": None,
            "edge_var": None}
    if st.get("edges") is not None:
        step["edges"] = _ids(st["edges"], f"{where}.edges")
    if st.get("edge_var") is not None:
        step["edge_var"] = _var(st["edge_var"], f"{where}.edge_var", taken)
    return step
