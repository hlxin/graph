"""Requests: what a consumer asks, normalized into one canonical form that the result echoes and identifies.

A request names its operation, the identity of the projection it was made for, its explicit scope and its limits.
Normalization checks its shape, fills every default explicitly, sorts and de-duplicates its sets, and gives it an
identity (``request_id``, a digest of the normalized form): two requests that ask the same thing normalize to the
same form and the same identity. Whether the nodes, relations, types and edges it names exist is checked against
the projection at execution (:func:`graph_runtime.execute`), after the projection identity itself.

Operations (docs/API_CONTRACT.md):

* ``reachability``: ``sources``, ``scope``, ``max_depth`` (null: unbounded); limits ``max_expansions``,
  ``max_results``.
* ``typed_path``: ``sources``, ``pattern`` (positions ``{scope, node_types}``), ``targets`` (null: any node);
  limit ``max_expansions``.
* ``shortest_path``: ``sources``, ``targets``, ``scope``, ``costs`` (``{"by_relation": {...}}`` or
  ``{"by_edge": {...}}``); limit ``max_expansions``.
* ``dependency_impact``: ``changed``, ``scope``, ``max_depth``; limits ``max_expansions``, ``max_results``.

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

from ._canonical import canonical_json, canonical_object, freeze, is_digest, sha256_text, thaw
from .errors import RequestError
from .projection import CONTRACT, DIRECTIONS

OPERATIONS = ("reachability", "typed_path", "shortest_path", "dependency_impact", "pattern")
PATTERN_ANSWERS = ("rows", "count", "exists")
MODES = ("exact",)
FAILED, UNSUPPORTED = "failed", "unsupported"
REQUEST_KIND = "graph_runtime.request"

DEFAULT_LIMITS = {"max_expansions": 1_000_000, "max_results": 100_000}
_LIMITS = {"reachability": ("max_expansions", "max_results"),
           "dependency_impact": ("max_expansions", "max_results"),
           "typed_path": ("max_expansions",),
           "shortest_path": ("max_expansions",),
           "pattern": ("max_expansions", "max_results")}
_PARAMS = {"reachability": (("sources", "scope"), ("max_depth",)),
           "dependency_impact": (("changed", "scope"), ("max_depth",)),
           "typed_path": (("sources", "pattern"), ("targets",)),
           "shortest_path": (("sources", "targets", "scope", "costs"), ()),
           "pattern": (("match",), ("steps", "answer"))}
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
    elif op == "pattern":
        data["match"], data["steps"] = _graph_pattern(raw["match"], raw.get("steps") or [])
        data["answer"] = raw.get("answer", "rows")
        if data["answer"] not in PATTERN_ANSWERS:
            raise _fail("invalid_request", f"answer is one of {list(PATTERN_ANSWERS)}, not {data['answer']!r}")
    else:
        data["sources"] = _ids(raw["sources"], "sources")
        data["targets"] = _ids(raw["targets"], "targets")
        data["scope"] = _scope(raw["scope"], "scope")
        data["costs"] = _costs(raw["costs"])
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


def _costs(value: Any) -> dict[str, dict[str, int | float]]:
    if not isinstance(value, Mapping) or len(value) != 1 or next(iter(value)) not in ("by_relation", "by_edge"):
        raise _fail("invalid_request", 'costs is {"by_relation": {relation: cost}} or {"by_edge": {edge: cost}}')
    kind, table = next(iter(value.items()))
    if not isinstance(table, Mapping):
        raise _fail("invalid_request", f"costs.{kind} is a mapping of IDs to costs")
    out = {}
    for key, x in table.items():
        if type(key) is not str or not key:
            raise _fail("invalid_request", f"costs.{kind} holds the key {key!r}; a key is a non-empty string")
        if type(x) is int and x >= 0 or type(x) is float and 0 <= x <= _FLOAT_MAX:
            out[key] = x                       # the common case, checked without a call
        else:
            out[key] = _cost(x, kind, key)
    return {kind: out}                         # canonical JSON sorts the keys; nothing else reads their order


_FLOAT_MAX = sys.float_info.max


def _cost(x: Any, kind: str, key: str) -> int | float:
    """A cost: an integer (any size, kept exact) or a finite float, never negative."""
    where = f"costs.{kind}[{key!r}]"
    if isinstance(x, bool) or not isinstance(x, (numbers.Integral, float)):
        raise _fail("invalid_cost", f"{where} is {x!r}; a cost is an integer or a finite float")
    x = int(x) if isinstance(x, numbers.Integral) else float(x)
    if isinstance(x, float) and not math.isfinite(x):
        raise _fail("non_finite_cost", f"{where} is {x!r}; {CONTRACT} accepts finite costs only")
    if x < 0:
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


def _node_constraint(raw: Mapping, where: str) -> dict:
    out = {}
    for field in ("node_types", "nodes"):
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
    """A tree pattern: a matched variable, then steps, each from a variable bound before it to a new one. A step from
    a variable an optional step binds is optional too, so that an unmatched optional part never removes a row."""
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
