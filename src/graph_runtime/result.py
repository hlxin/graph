"""Results: one contract for every operation and every backend.

Every result says how the operation ended (``status``), whether its answer is complete, and what it found:

* ``completed``: the operation ran to its end; the answer is complete for this request on this projection. A
  complete negative (nothing reached, no witness) is scoped to the requested operation on the declared projection:
  it is not an absence claim about the literature or the physical world.
* ``interrupted``: a limit (``expansion_budget``, ``output_limit``) or the caller (``deadline``, ``cancelled``)
  stopped it first. What it lists is valid, each item with its witness, but nothing is certified absent, even when
  some witnesses were already found.
* ``unsupported``: the request asks for what the contract does not provide; ``failed``: the request is malformed or
  names what the projection does not hold (or the runtime failed); ``stale_snapshot``: the request was made for
  another projection, or the projection no longer matches what it was built from. These carry no answer.

``complete`` is true exactly when the status is ``completed``. ``result_id`` is a digest of everything but
``timing`` (wall-clock measurements), so the same runtime, projection and request give the same ``result_id``
unless a deadline or a cancellation interrupted it.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from fractions import Fraction
from types import MappingProxyType
from typing import Any

from ._canonical import canonical_json, canonical_object, digest, encode_text, freeze, sha256_text, thaw
from ._version import RUNTIME_NAME, __version__
from ._validation import shape_problems
from .projection import CONTRACT
from .errors import RequestError

COMPLETED, INTERRUPTED, UNSUPPORTED, FAILED, STALE = (
    "completed", "interrupted", "unsupported", "failed", "stale_snapshot")
STATUSES = (COMPLETED, INTERRUPTED, UNSUPPORTED, FAILED, STALE)
INTERRUPTIONS = ("expansion_budget", "output_limit", "deadline", "cancelled")
RESULT_KIND = "graph_runtime.result"
_SET_OPERATIONS = {"reachability": "reached", "dependency_impact": "affected"}
_WITNESS_OPERATIONS = ("typed_path", "shortest_path")


class Result:
    """A read-only result. :meth:`to_dict` gives its JSON form; :meth:`from_dict` reads one back and checks its
    ``result_id``.

    Each top-level field is encoded as canonical JSON once, when the result is made: those encodings give the
    ``result_id`` and :meth:`to_json` without encoding the answer again, and the request's own encoding (made for its
    ``request_id``) is reused as it is. The read-only views the properties return are made on first use."""

    __slots__ = ("_data", "_parts", "_frozen", "_timing", "_index")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("results are made by graph_runtime.execute(...) or read with Result.from_dict(...)")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a Result is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("a Result is immutable")

    @classmethod
    def _create(cls, *, status: str, operation: str | None = None, request: Any = None, projection: str | None = None,
                answer: Mapping | None = None, interruption: Mapping | None = None, error: Mapping | None = None,
                stats: Mapping | None = None, limitations: list[str] | tuple[str, ...] = (),
                timing: Mapping | None = None, started: float | None = None) -> "Result":
        """``timing``: the seconds each phase took; ``started`` (a ``time.perf_counter()`` value): when the execution
        began, so that ``total_s`` covers making the result too. The answer and other values are taken over, not
        copied: the engine hands over values it no longer holds."""
        made = time.perf_counter()
        if status not in STATUSES:
            raise ValueError(f"status {status!r} is not one of {STATUSES}")
        data = {"kind": RESULT_KIND, "contract": CONTRACT,
                "runtime": {"name": RUNTIME_NAME, "version": __version__},
                "status": status, "complete": status == COMPLETED, "operation": operation,
                "mode": request["mode"] if request is not None else None, "projection": projection,
                "request": request._data if request is not None else None,
                "request_id": request.request_id if request is not None else None,
                "answer": answer, "interruption": interruption, "error": error,
                "stats": dict(stats or {"expansions": 0, "edges_examined": 0, "discovered": 0}),
                "limitations": list(limitations)}
        parts = {k: canonical_json(v) for k, v in data.items() if k != "request"}
        parts["request"] = request._json if request is not None else "null"
        rid = sha256_text(canonical_object(parts))
        data["result_id"], parts["result_id"] = rid, encode_text(rid)
        r = cls._wrap(data, parts, None)
        if timing or started is not None:
            done = time.perf_counter()
            t = {**(timing or {}), "result_s": done - made}
            if started is not None:
                t["total_s"] = done - started
            object.__setattr__(r, "_timing", freeze(t))
        return r

    @classmethod
    def _wrap(cls, data: dict, parts: dict, timing: Mapping | None) -> "Result":
        r = object.__new__(cls)
        object.__setattr__(r, "_data", data)
        object.__setattr__(r, "_parts", parts)
        object.__setattr__(r, "_frozen", {})
        object.__setattr__(r, "_timing", freeze(dict(timing or {})))
        object.__setattr__(r, "_index", None)
        return r

    @classmethod
    def from_dict(cls, data: Mapping) -> "Result":
        """Read a result's JSON form (another backend's, say): its ``result_id`` must match its content (``timing``
        aside) and it must keep the contract's invariants (:func:`contract_problems`)."""
        if not isinstance(data, Mapping) or data.get("kind") != RESULT_KIND:
            raise ValueError(f"a result has kind {RESULT_KIND!r}")
        body = {k: thaw(v) for k, v in data.items() if k not in ("result_id", "timing")}
        try:
            parts = {k: canonical_json(v) for k, v in body.items()}
            matches = data.get("result_id") == sha256_text(canonical_object(parts))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"the result is not plain JSON: {exc}") from None
        if not matches:
            raise ValueError("the result's content does not match its result_id")
        problems = _checked({**body, "result_id": data["result_id"], **({"timing": thaw(data["timing"])}
                                                                          if "timing" in data else {})})
        if problems:
            raise ValueError("the result breaks the contract: " + "; ".join(problems))
        parts["result_id"] = encode_text(data["result_id"])
        return cls._wrap({**body, "result_id": data["result_id"]}, parts, data.get("timing"))

    # ------------------------------------------------------------------------------------------------ fields
    def _get(self, field: str) -> Any:
        """A field as a read-only value, made on first use."""
        value = self._data[field]
        if value is None or type(value) in (str, bool, int, MappingProxyType):
            return value
        cache = self._frozen
        if field not in cache:
            cache[field] = freeze(value)
        return cache[field]

    @property
    def status(self) -> str:
        return self._data["status"]

    @property
    def complete(self) -> bool:
        return self._data["complete"]

    @property
    def operation(self) -> str | None:
        return self._data["operation"]

    @property
    def projection(self) -> str | None:
        return self._data["projection"]

    @property
    def request(self) -> Mapping | None:
        return self._get("request")

    @property
    def request_id(self) -> str | None:
        return self._data["request_id"]

    @property
    def answer(self) -> Mapping | None:
        return self._get("answer")

    @property
    def interruption(self) -> Mapping | None:
        return self._get("interruption")

    @property
    def error(self) -> Mapping | None:
        return self._get("error")

    @property
    def stats(self) -> Mapping:
        return self._get("stats")

    @property
    def limitations(self) -> tuple[str, ...]:
        return self._get("limitations")

    @property
    def result_id(self) -> str:
        return self._data["result_id"]

    @property
    def timing(self) -> Mapping:
        """Wall-clock seconds by phase; not part of ``result_id``."""
        return self._timing

    def to_dict(self, *, timing: bool = True) -> dict:
        out = {k: thaw(v) for k, v in self._data.items()}
        if timing:
            out["timing"] = thaw(self._timing)
        return out

    def to_json(self, *, timing: bool = False, indent: int | None = None) -> str:
        """The JSON form; canonical (sorted keys, compact) unless ``indent`` is given."""
        if indent is None:
            parts = self._parts if not timing else {**self._parts, "timing": canonical_json(thaw(self._timing))}
            return canonical_object(parts)
        import json
        return json.dumps(self.to_dict(timing=timing), indent=indent, sort_keys=True, ensure_ascii=False)

    def __repr__(self) -> str:
        return f"<Result {self.operation} {self.status} complete={self.complete}>"

    # ------------------------------------------------------------------------------------------------ reading answers
    def _entries(self) -> Mapping[str, Mapping]:
        key = _SET_OPERATIONS.get(self.operation or "")
        if key is None:
            raise ValueError(f"a {self.operation} result lists no node set")
        if self._index is None:
            items = (self.answer or {}).get(key) or ()
            object.__setattr__(self, "_index", freeze({e["node"]: e for e in items}))
        return self._index

    def reachable(self, node_id: str) -> bool | None:
        """For reachability: True when ``node_id`` was reached, False only when the answer is complete and it was
        not, None when that is unknown (an interrupted search, or a result with no answer)."""
        if self.operation != "reachability":
            raise ValueError(f"reachable() reads a reachability result, not {self.operation}")
        return self._member(node_id)

    def affected(self, node_id: str) -> bool | None:
        """For dependency impact: True, False (only when complete) or None (unknown), as :meth:`reachable`."""
        if self.operation != "dependency_impact":
            raise ValueError(f"affected() reads a dependency_impact result, not {self.operation}")
        return self._member(node_id)

    def _member(self, node_id: str) -> bool | None:
        if self.answer is None:
            return None
        if node_id in self._entries():
            return True
        return False if self.status == COMPLETED else None

    def path_to(self, node_id: str) -> list[dict]:
        """The witness of a reached (or affected) node: its hops ``{edge, relation, direction, from, to}`` from a
        source (or a changed node), reconstructed from the answer's ``via`` links."""
        entries = self._entries()
        if node_id not in entries:
            raise KeyError(f"{node_id!r} is not in this result's answer")
        hops: list[dict] = []
        node, entry = node_id, entries[node_id]
        for _ in range(entries[node_id]["depth"]):        # a node at depth d is d hops from where it was reached
            if entry is None or entry["via"] is None:
                raise ValueError(f"the answer holds no complete witness for {node_id!r}")
            via = entry["via"]
            hops.append({"edge": via["edge"], "relation": via["relation"], "direction": via["direction"],
                         "from": via["from"], "to": node})
            node = via["from"]
            entry = entries.get(node)       # a changed node that nothing reached has no entry; it ends the witness
        hops.reverse()
        return hops

    @property
    def found(self) -> bool | None:
        """For typed and shortest paths: True (a witness is given), False (only when complete) or None (unknown)."""
        if self.operation not in _WITNESS_OPERATIONS:
            raise ValueError(f"found reads a typed_path or shortest_path result, not {self.operation}")
        found = None if self.answer is None else self.answer["found"]
        return None if found is False and self.status != COMPLETED else found

    @property
    def witness(self) -> list[dict] | None:
        """For typed and shortest paths: the witness hops, or None."""
        if self.operation not in _WITNESS_OPERATIONS:
            raise ValueError(f"witness reads a typed_path or shortest_path result, not {self.operation}")
        w = (self.answer or {}).get("witness")
        return None if w is None else thaw(w["hops"])


def contract_problems(data: Mapping) -> list[str]:
    """What in a result's JSON breaks the contract's invariants, read without the input records (empty when it keeps
    them): the status, ``complete`` exactly for ``completed``, an answer exactly when the request ran, an
    interruption exactly when interrupted, an error exactly when not run, and ``found: false`` (or a proven optimum)
    only in a complete answer. :func:`graph_runtime.verify_result` adds the witnesses, checked against the records."""
    return _checked(thaw(data))


def _checked(data: dict) -> list[str]:
    """:func:`contract_problems` of plain data the caller owns (no copy is made)."""
    try:
        return _contract_problems(data)
    except (TypeError, ValueError, KeyError, OverflowError, RecursionError, RequestError) as exc:
        return [f"malformed result ({type(exc).__name__}: {exc})"]


def _contract_problems(data: dict) -> list[str]:
    if not isinstance(data, dict):
        return ["a result is a mapping"]
    status = data.get("status")
    if status not in STATUSES:
        return [f"status {status!r} is not one of {list(STATUSES)}"]
    out = shape_problems("result", data)
    if data.get("complete") is not (status == COMPLETED):
        out.append("complete is true exactly when the status is completed")
    ran = status in (COMPLETED, INTERRUPTED)
    answer = data.get("answer")
    if ran != isinstance(answer, Mapping):
        out.append(f"a {status} result {'carries an answer' if ran else 'carries no answer'}")
    if (status == INTERRUPTED) != isinstance(data.get("interruption"), Mapping):
        out.append("an interrupted result, and only one, says what interrupted it")
    if ran == isinstance(data.get("error"), Mapping):
        out.append(f"a {status} result {'carries no error' if ran else 'carries an error with its code'}")
    if isinstance(answer, Mapping) and data.get("operation") in _WITNESS_OPERATIONS:
        found = answer.get("found")
        if found is not True and found is not False and found is not None:
            out.append(f"found is true, false or null, not {found!r}")
        if found is False and status != COMPLETED:
            out.append("found is false only in a complete answer")
        if found is None and status != INTERRUPTED:
            out.append("found is unknown (null) only in an interrupted answer")
        if data.get("operation") == "shortest_path" and found is True \
                and answer.get("optimal") is not (status == COMPLETED):
            out.append("a witness is proven optimal exactly when the search completed")
    if out:
        return out
    body = {k: v for k, v in data.items() if k not in ("result_id", "timing")}
    if data["result_id"] != digest(body):
        out.append("the result's content does not match its result_id")
    request = data["request"]
    if request is None:
        return out
    from .request import normalize_request
    req = normalize_request(request)
    if canonical_json(req.to_dict()) != canonical_json(request):
        out.append("the echoed request must be normalized")
    if req.request_id != data["request_id"]:
        out.append("the request's content does not match its request_id")
    mismatch = data["projection"] != req.projection
    stale_mismatch = status == STALE and data["error"]["code"] == "projection_mismatch"
    if mismatch != stale_mismatch:
        out.append("projection differs from the request only for projection_mismatch")
    if not ran:
        return out
    stats, limits = data["stats"], request["limits"]
    if any(type(value) is not int for value in stats.values()):
        out.append("stats counters must be integers")
    if stats["expansions"] > limits["max_expansions"]:
        out.append("expansions exceed max_expansions")
    if status == INTERRUPTED:
        interruption = data["interruption"]
        reason = interruption["reason"]
        if type(interruption["expansions"]) is not int or (interruption["limit"] is not None
                                                         and type(interruption["limit"]) is not int):
            out.append("interruption counts must be integers")
        if interruption["expansions"] != stats["expansions"]:
            out.append("interruption and stats disagree on expansions")
        limit_key = {"expansion_budget": "max_expansions", "output_limit": "max_results"}.get(reason)
        if limit_key and (limit_key not in limits or interruption["limit"] != limits[limit_key]):
            out.append("interruption limit disagrees with the request")
        if reason == "expansion_budget" and stats["expansions"] != limits["max_expansions"]:
            out.append("expansion_budget requires an exhausted budget")
    op = data["operation"]
    if op in _SET_OPERATIONS:
        entries = answer[_SET_OPERATIONS[op]]
        if type(answer["count"]) is not int or any(type(e["depth"]) is not int for e in entries):
            out.append("answer counts and depths must be integers")
        names = [e["node"] for e in entries]
        if names != sorted(set(names)):
            out.append("answer nodes must be unique and in ascending ID order")
        if answer["count"] != len(entries) or len(entries) > limits["max_results"]:
            out.append("answer count must match its entries and fit max_results")
        if status == INTERRUPTED and data["interruption"]["reason"] == "output_limit" \
                and len(entries) != limits["max_results"]:
            out.append("output_limit requires a full output buffer")
        strict = op == "dependency_impact"
        seeds = set(request["changed" if strict else "sources"])
        if strict and answer["changed"] != request["changed"]:
            out.append("changed must match the request")
        by_node = {e["node"]: e for e in entries}
        allowed = {(s["relation"], s["direction"]) for s in request["scope"]}
        for entry in entries:
            node, depth, via = entry["node"], entry["depth"], entry["via"]
            if request["max_depth"] is not None and depth > request["max_depth"]:
                out.append("answer depth exceeds max_depth")
            if depth == 0:
                if node not in seeds:
                    out.append("a depth-zero node must be a source")
                continue
            if (via["relation"], via["direction"]) not in allowed:
                out.append("a via hop is outside the request's scope")
            parent = by_node.get(via["from"])
            # Impact paths end at a changed node at expansion depth zero, even if a cycle also reaches it.
            if strict and depth == 1:
                valid_parent = via["from"] in seeds
            else:
                valid_parent = parent is not None and parent["depth"] == depth - 1
            if not valid_parent:
                out.append("a via chain must decrease depth and end at a requested source")
        if not strict and status == COMPLETED and any(by_node.get(s, {}).get("depth") != 0 for s in seeds):
            out.append("a complete reachability answer includes every source at depth zero")
    elif answer["found"] is True:
        witness = answer["witness"]
        hops = witness["hops"]
        if type(witness["length"]) is not int or witness["length"] != len(hops):
            out.append("witness length must match its hops")
        if witness["source"] not in request["sources"]:
            out.append("a witness must start at a requested source")
        if request["targets"] is not None and witness["target"] not in request["targets"]:
            out.append("a witness must end at a requested target")
        at = witness["source"]
        for hop in hops:
            if hop["from"] != at:
                out.append("witness hops must chain")
            at = hop["to"]
        if at != witness["target"]:
            out.append("witness hops must end at the witness target")
        if op == "typed_path":
            if len(hops) != len(request["pattern"]):
                out.append("a typed witness must match the pattern length")
            for i, (hop, pos) in enumerate(zip(hops, request["pattern"])):
                if hop["position"] != i or type(hop["position"]) is not int:
                    out.append("typed hop positions must be consecutive integers")
                if {"relation": hop["relation"], "direction": hop["direction"]} not in pos["scope"]:
                    out.append("a typed hop must match its pattern scope")
    if op == "shortest_path":
        out.extend(_cost_problems(request, answer, status))
    return out


def _cost_problems(request: dict, answer: dict, status: str) -> list[str]:
    out = []
    (kind, table), = request["costs"].items()
    rational = any(isinstance(v, float) for v in table.values())

    def number(field):
        value = answer[field]
        if not rational:
            if type(value) is not int or f"{field}_exact" in answer:
                raise ValueError(f"{field} must be an integer in integer-cost mode")
            return value
        exact = answer[f"{field}_exact"]
        number = Fraction(exact)
        if str(number) != exact:
            raise ValueError(f"{field}_exact must be a non-negative rational in lowest terms")
        try:
            nearest = float(number)
        except OverflowError:
            nearest = None
        if value != nearest:
            out.append(f"{field} must be the nearest float to {field}_exact (null on overflow)")
        return number

    lower = number("lower_bound") if status == INTERRUPTED else None
    if answer["found"] is not True:
        return out
    cost = number("cost")
    if lower is not None and lower > cost:
        out.append("lower_bound exceeds the witness cost")
    total = 0
    for hop in answer["witness"]["hops"]:
        if {"relation": hop["relation"], "direction": hop["direction"]} not in request["scope"]:
            out.append("a shortest path hop must be in scope")
        expected = table.get(hop["relation"] if kind == "by_relation" else hop["edge"])
        if expected is None or hop["cost"] != expected or type(hop["cost"]) is not type(expected):
            out.append("a hop cost must match the request")
        total += Fraction(hop["cost"]) if rational else hop["cost"]
    if total != cost:
        out.append("the witness cost must equal the exact sum of hop costs")
    return out
