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
_WITNESS_OPERATIONS = ("typed_path", "shortest_path", "bottleneck_path")


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
        if self.operation == "regular_path" and "reached" in (self.answer or {}):
            key = "reached"
        if key is None:
            raise ValueError(f"a {self.operation} result lists no node set")
        if self._index is None:
            items = (self.answer or {}).get(key) or ()
            object.__setattr__(self, "_index", freeze({e["node"]: e for e in items}))
        return self._index

    def reachable(self, node_id: str) -> bool | None:
        """For reachability (and a regular path's ``reached`` answer): True when ``node_id`` was reached, False only
        when the answer is complete and it was not, None when that is unknown (an interrupted search, or a result with
        no answer)."""
        if self.operation not in ("reachability", "regular_path"):
            raise ValueError(f"reachable() reads a reachability or regular_path result, not {self.operation}")
        if self.operation == "regular_path" and self.answer is not None and "reached" not in self.answer:
            raise ValueError("reachable() reads a regular_path result whose answer is reached, not witness")
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
        source (or a changed node), reconstructed from the answer's ``via`` links (for a regular path, from its tree,
        each hop with the automaton ``state`` it moves to)."""
        entries = self._entries()
        if node_id not in entries:
            raise KeyError(f"{node_id!r} is not in this result's answer")
        if self.operation == "regular_path":
            tree = {(t["node"], t["state"]): t for t in self.answer["tree"]}
            hops: list[dict] = []
            key = (node_id, entries[node_id]["state"])
            while tree[key]["via"] is not None:
                via = tree[key]["via"]
                hops.append({"edge": via["edge"], "relation": via["relation"], "direction": via["direction"],
                             "from": via["from"], "to": key[0], "state": key[1]})
                key = (via["from"], via["from_state"])
            hops.reverse()
            return hops
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
        """For typed, shortest, bottleneck and regular paths (a ``witness`` answer): True (a witness is given), False
        (only when complete) or None (unknown)."""
        if self.operation not in _WITNESS_OPERATIONS and not (
                self.operation == "regular_path" and "found" in (self.answer or {"found": None})):
            raise ValueError(f"found reads a path result with a witness, not {self.operation}")
        found = None if self.answer is None else self.answer["found"]
        return None if found is False and self.status != COMPLETED else found

    @property
    def exists(self) -> bool | None:
        """For patterns: True (rows exist), False (only when complete) or None (unknown)."""
        if self.operation != "pattern":
            raise ValueError(f"exists reads a pattern result, not {self.operation}")
        exists = None if self.answer is None else self.answer["exists"]
        return None if exists is False and self.status != COMPLETED else exists

    @property
    def witness(self) -> list[dict] | None:
        """For typed, shortest, bottleneck and regular paths (a ``witness`` answer): the witness hops, or None."""
        if self.operation not in _WITNESS_OPERATIONS and self.operation != "regular_path":
            raise ValueError(f"witness reads a path result with a witness, not {self.operation}")
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
        if data.get("operation") in ("shortest_path", "bottleneck_path") and found is True \
                and answer.get("optimal") is not (status == COMPLETED and answer.get("negative_cycle") is None):
            out.append("a witness is proven optimal exactly when the search completed (and no negative cycle makes "
                       "walks ever cheaper)")
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
        # whether a hop's relation is in scope depends on the projection's relation hierarchy: verify_result checks
        # it against the records; here, only its direction
        directions = {s["direction"] for s in request["scope"]}
        for entry in entries:
            node, depth, via = entry["node"], entry["depth"], entry["via"]
            if request["max_depth"] is not None and depth > request["max_depth"]:
                out.append("answer depth exceeds max_depth")
            if depth == 0:
                if node not in seeds:
                    out.append("a depth-zero node must be a source")
                continue
            if via["direction"] not in directions:
                out.append("a via hop goes in a direction the scope does not follow")
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
    elif op == "pattern":
        out.extend(_pattern_problems(request, answer, status, data.get("interruption")))
    elif op == "support":
        out.extend(_support_problems(request, answer, status, data.get("interruption")))
    elif op == "regular_path":
        out.extend(_regular_problems(request, answer, status, data.get("interruption")))
    elif op == "derivation":
        out.extend(_derivation_problems(request, answer, status))
    elif op == "periodicity":
        out.extend(_periodicity_problems(request, answer, status))
    elif op == "personalized_pagerank":
        out.extend(_pagerank_problems(request, answer))
    elif op == "components":
        out.extend(_components_problems(request, answer, status, data.get("interruption")))
    elif op == "biconnectivity":
        out.extend(_biconnectivity_problems(request, answer, status))
    elif op == "max_flow":
        out.extend(_flow_problems(request, answer, status))
    elif op == "matching":
        out.extend(_matching_problems(request, answer, status))
    elif op == "dominators":
        out.extend(_dominator_problems(request, answer, status))
    elif op == "incidence":
        out.extend(_incidence_problems(request, answer, status))
    elif op == "critical_path":
        out.extend(_critical_problems(request, answer, status))
    elif op == "d_separation":
        out.extend(_separation_problems(request, answer, status))
    elif op == "spanning_forest":
        out.extend(_forest_problems(request, answer, status))
    elif op == "cycle_basis":
        out.extend(_cycle_problems(request, answer, status))
    elif op == "k_shortest_paths":
        out.extend(_kpaths_problems(request, answer, status))
    elif op == "min_cost_flow":
        out.extend(_mincost_problems(request, answer, status))
    elif op == "reachability_pairs":
        out.extend(_pairs_problems(request, answer, status))
    elif answer.get("negative_cycle") is not None:          # a shortest path made ever cheaper: three walks
        nc = answer["negative_cycle"]
        start = nc["cycle"]["source"]
        for name, begin, end in (("lead_in", set(request["sources"]), {start}), ("cycle", {start}, {start}),
                                 ("lead_out", {start}, set(request["targets"]))):
            walk = nc[name]
            if type(walk["length"]) is not int or walk["length"] != len(walk["hops"]):
                out.append(f"negative_cycle.{name}: its length must match its hops")
            if walk["source"] not in begin or walk["target"] not in end:
                out.append(f"negative_cycle.{name} runs from a source to the cycle, around it, or from it to a target")
            at = walk["source"]
            for hop in walk["hops"]:
                if hop["from"] != at:
                    out.append(f"negative_cycle.{name}: its hops must chain")
                at = hop["to"]
            if at != walk["target"]:
                out.append(f"negative_cycle.{name}: its hops must end where it ends")
        if not nc["cycle"]["hops"]:
            out.append("the negative cycle has at least one hop")
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
                if hop["direction"] not in {s["direction"] for s in pos["scope"]}:
                    out.append("a typed hop goes in a direction its pattern position does not follow")
    if op == "shortest_path":
        out.extend(_cost_problems(request, answer, status))
    if op == "bottleneck_path":
        out.extend(_bottleneck_problems(request, answer, status))
    if op in _WITNESS_OPERATIONS:
        out.extend(_certificate_problems(op, request, answer, status))
    return out


def _regular_problems(request: dict, answer: dict, status: str, interruption: dict | None) -> list[str]:
    """A regular path answer's own consistency, read without the records: a witness chains from a source to a target
    with one automaton state per hop; reached nodes are unique, in ascending ID order, within the targets and the
    output limit, each named by a branch of the tree that leads back to a source at the start state; a certificate
    appears exactly when asked for and there is something to prove, in canonical form. Whether hops match the path
    and the certificate is closed is checked against the records (:func:`graph_runtime.verify_result`)."""
    out = []
    sources = set(request["sources"])
    targets = None if request["targets"] is None else set(request["targets"])
    cert = answer.get("certificate")
    if request["answer"] == "witness":
        if "found" not in answer:
            return ["a regular path witness answer says whether it found a walk"]
        found = answer["found"]
        if found is False and status != COMPLETED:
            out.append("found is false only in a complete answer")
        if found is None and status != INTERRUPTED:
            out.append("found is unknown (null) only in an interrupted answer")
        hops: list = []
        if found is True:
            witness = answer["witness"]
            hops = witness["hops"]
            if type(witness["length"]) is not int or witness["length"] != len(hops):
                out.append("witness length must match its hops")
            if witness["source"] not in sources:
                out.append("a witness must start at a requested source")
            if targets is not None and witness["target"] not in targets:
                out.append("a witness must end at a requested target")
            at = witness["source"]
            for hop in hops:
                if hop["from"] != at or type(hop["state"]) is not int or hop["state"] < 1:
                    out.append("witness hops must chain, each moving to an automaton state")
                at = hop["to"]
            if at != witness["target"]:
                out.append("witness hops must end at the witness target")
        want = None
        if request["certificate"] and status == COMPLETED and (found is False or (found is True and hops)):
            want = len(hops) if found else None
            if cert is None or cert["bound"] != want:
                out.append("a requested certificate comes with a complete negative (bound null) and with a found "
                           "witness of at least one hop (bound: its length)")
        elif cert is not None:
            out.append("a certificate comes only when asked for, with a complete negative or a found witness of at "
                       "least one hop")
    else:
        if "reached" not in answer:
            return ["a regular path reached answer lists the nodes reached"]
        entries, tree = answer["reached"], answer["tree"]
        names = [e["node"] for e in entries]
        if names != sorted(set(names)):
            out.append("reached nodes must be unique and in ascending ID order")
        if answer["count"] != len(entries) or len(entries) > request["limits"]["max_results"]:
            out.append("count must match the reached entries and fit max_results")
        if status == INTERRUPTED and (interruption or {}).get("reason") == "output_limit" \
                and len(entries) != request["limits"]["max_results"]:
            out.append("output_limit requires a full output buffer")
        if targets is not None and not set(names) <= targets:
            out.append("reached nodes must be requested targets")
        keys = [(t["node"], t["state"]) for t in tree]
        if keys != sorted(set(keys)):
            out.append("tree entries must be unique, by node then state")
        by_key = {(t["node"], t["state"]): t for t in tree}
        for e in entries:
            t = by_key.get((e["node"], e["state"]))
            if t is None or t["length"] != e["length"]:
                out.append(f"reached {e['node']!r} is not named by a tree entry with its state and length")
        for t in tree:
            via = t["via"]
            if via is None:
                if t["length"] != 0 or t["state"] != 0 or t["node"] not in sources:
                    out.append("a tree entry without a hop is a source at the start state, with no hop")
                continue
            parent = by_key.get((via["from"], via["from_state"]))
            if parent is None or parent["length"] != t["length"] - 1 or t["state"] < 1:
                out.append("a tree entry's hop must come from a tree entry one hop shorter")
        if request["certificate"] and status == COMPLETED:
            if cert is None or cert["bound"] is not None:
                out.append("a requested certificate of a complete reached answer lists every product state (bound "
                           "null)")
        elif cert is not None:
            out.append("a certificate comes only when asked for, with a complete answer")
    if cert is not None:
        states = [(st["state"], st["node"]) for st in cert["states"]]
        if states != sorted(set(states)):
            out.append("certificate states are unique, by state then node ID")
        if not {(0, s) for s in sources} <= set(states) or any(
                (st["state"], st["node"]) in {(0, s) for s in sources} and st["length"] != 0 for st in cert["states"]):
            out.append("certificate states start with every source at the start state, with no hop")
        if cert["bound"] is not None and any(st["length"] >= cert["bound"] for st in cert["states"]):
            out.append("certificate states have fewer hops than its bound")
    return out


def _pagerank_problems(request: dict, answer: dict) -> list[str]:
    """A ranking's own consistency, read without the records: exact numbers written in lowest terms with their
    nearest floats; every interval non-empty; entries unique, by lower bound then node ID, at most ``top`` of them;
    a certified prefix the listed intervals do not contradict; a certificate exactly when asked for, giving each listed
    node its lower bound as its estimate. Whether the intervals hold is checked against the records
    (:func:`graph_runtime.verify_result`) from the certificate."""
    out = []

    def read(entry: Mapping, field: str) -> Fraction:
        exact = entry[f"{field}_exact"]
        number = Fraction(exact)
        if str(number) != exact or number < 0 or entry[field] != _nearest(number):
            raise ValueError(f"{field}_exact is a non-negative rational in lowest terms, {field} its nearest float")
        return number

    try:
        ranked = [(read(e, "lower"), read(e, "upper"), e["node"]) for e in answer["ranked"]]
        read(answer, "residual")
        values = {e["node"]: read(e, "value") for e in (answer["certificate"] or {}).get("values", ())}
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        return [f"ranking: {exc}"]
    if answer["count"] != len(ranked) or len(ranked) > request["top"]:
        out.append("count must match the ranked entries and fit top")
    if any(lo > hi for lo, hi, _ in ranked):
        out.append("every interval has its lower bound at most its upper bound")
    nodes = [n for _, _, n in ranked]
    if len(set(nodes)) != len(nodes) or ranked != sorted(ranked, key=lambda x: (-x[0], x[2])):
        out.append("ranked nodes are unique, by lower bound (highest first) then node ID")
    m = answer["certified_top"]
    if type(m) is not int or not 0 <= m <= len(ranked) or (m and min(lo for lo, _, _ in ranked[:m]) <= max(
            [hi for _, hi, _ in ranked[m:]] or [Fraction(-1)])):
        out.append("the certified prefix is part of the list, and its lower bounds clear the other listed intervals")
    cert = answer["certificate"]
    if (cert is not None) != bool(request["certificate"]):
        out.append("a certificate comes exactly when asked for")
    elif cert is not None:
        names = [e["node"] for e in cert["values"]]
        if names != sorted(set(names)) or any(v == 0 for v in values.values()):
            out.append("certificate estimates are unique, nonzero, in ascending node ID order")
        if any(values.get(n, Fraction(0)) != lo for lo, _, n in ranked):
            out.append("each listed lower bound is the node's estimate")
    return out


def _components_problems(request: dict, answer: dict, status: str, interruption: dict | None) -> list[str]:
    """A components answer's own consistency, read without the records: components disjoint, each its nodes unique and
    ascending, listed by first node, at least ``min_size`` of them and holding a requested node when nodes are named,
    within ``max_results``; a strong component of two nodes or more is cyclic; ``total`` known exactly when complete;
    a certificate exactly when asked for, whose groups (each with its trees) include every listed component. Whether they are the components is checked against the records
    (:func:`graph_runtime.verify_result`)."""
    out = []
    comps, want, strong = answer["components"], request["nodes"], request["connectivity"] == "strong"
    if answer["count"] != len(comps) or len(comps) > request["limits"]["max_results"]:
        out.append("count must match the components and fit max_results")
    if interruption is not None and interruption["reason"] == "output_limit" \
            and len(comps) != request["limits"]["max_results"]:
        out.append("output_limit requires a full output buffer")
    seen: set = set()
    for c in comps:
        nodes = c["nodes"]
        if nodes != sorted(set(nodes)) or c["size"] != len(nodes) or seen.intersection(nodes):
            out.append("a component's nodes are unique, ascending, its size their number, and in no other component")
        seen.update(nodes)
        if len(nodes) < request["min_size"] or (want is not None and not set(want).intersection(nodes)):
            out.append("a listed component has min_size nodes or more, and holds a requested node when nodes are named")
        flags = {"cyclic", "initial", "terminal"} & set(c)
        if flags != ({"cyclic", "initial", "terminal"} if strong else set()) \
                or (strong and len(nodes) > 1 and c["cyclic"] is not True):
            out.append("strong components (only) say whether they are cyclic, initial and terminal; one of two nodes "
                       "or more is cyclic")
    firsts = [c["nodes"][0] for c in comps if c["nodes"]]
    if firsts != sorted(firsts):
        out.append("components are listed in ascending order of their first node")
    total = answer["total"]
    if (total is None) != (status != COMPLETED) or (total is not None and (type(total) is not int
                                                                           or total < len(comps))):
        out.append("total is known (and at least count) exactly when the answer is complete")
    cert = answer["certificate"]
    if (cert is not None) != bool(request["certificate"]):
        out.append("a certificate comes exactly when asked for")
    elif cert is not None:
        if any((g["in"] is None) == strong for g in cert["groups"]):
            out.append("every group of the certificate has a tree out of its first node (strong: and one into it)")
        groups = {tuple(g["nodes"]) for g in cert["groups"]}
        if any(tuple(c["nodes"]) not in groups for c in comps):
            out.append("every listed component is one of the certificate's groups")
    return out


def _biconnectivity_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A biconnectivity answer's own consistency, read without the records: articulation points unique, ascending,
    each splitting its component into two pieces or more; bridges unique and ascending; blocks (exactly when asked
    for) with ascending unique nodes and edges, no edge in two blocks, listed by first edge, every bridge a block of
    its own; the number of components known exactly when complete; a certificate exactly when asked for. Whether they
    hold is checked against the records (:func:`graph_runtime.verify_result`)."""
    out = []
    points = [x["node"] for x in answer["articulation_points"]]
    if points != sorted(set(points)) or any(type(x["splits"]) is not int or x["splits"] < 2
                                            for x in answer["articulation_points"]):
        out.append("articulation points are unique, ascending, each splitting its component into two pieces or more")
    bridges = [b["edge"] for b in answer["bridges"]]
    if bridges != sorted(set(bridges)):
        out.append("bridges are unique and in ascending edge ID order")
    blocks = answer["blocks"]
    if (blocks is not None) != bool(request["blocks"]):
        out.append("blocks are listed exactly when asked for")
    elif blocks is not None:
        used: set = set()
        for b in blocks:
            if not b["edges"] or b["edges"] != sorted(set(b["edges"])) or b["nodes"] != sorted(set(b["nodes"])) \
                    or used.intersection(b["edges"]):
                out.append("a block has edges, its nodes and edges unique and ascending, no edge in another block")
            used.update(b["edges"])
        if [b["edges"][0] for b in blocks if b["edges"]] != sorted(b["edges"][0] for b in blocks if b["edges"]):
            out.append("blocks are listed in ascending order of their first edge")
        singles = {b["edges"][0] for b in blocks if len(b["edges"]) == 1}
        if status == COMPLETED and not set(bridges) <= singles:
            out.append("every bridge is a block of its own")
    count = answer["components"]
    if (count is None) != (status != COMPLETED) or (count is not None and type(count) is not int):
        out.append("components is counted exactly when the answer is complete")
    if (answer["certificate"] is not None) != bool(request["certificate"]):
        out.append("a certificate comes exactly when asked for")
    return out


def _flow_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A max-flow answer's own consistency, read without the records: numbers exact (integers, or nearest floats with
    ``p/q`` beside them when a capacity is a float); flows positive, one per edge, in ascending edge order; the value
    at most the upper bound, equal to it and optimal exactly when complete; a complete answer's cut adding up to the
    value; a certificate only on a complete answer, exactly when asked for. Whether the flow and the cut hold is
    checked against the records (:func:`graph_runtime.verify_result`)."""
    out = []
    rational = any(isinstance(x, float) for x in (next(iter(request["capacities"].values())).values()
                                                     if request["capacities"] is not None else ())) or \
        isinstance(request["node_capacity"], float)

    def read(entry: Mapping, field: str) -> Fraction:
        if rational:
            exact = entry[f"{field}_exact"]
            number = Fraction(exact)
            if str(number) != exact or entry[field] != _nearest(number):
                raise ValueError(f"{field}_exact is a rational in lowest terms, {field} its nearest float")
            return number
        if type(entry[field]) is not int or f"{field}_exact" in entry:
            raise ValueError(f"{field} is an integer when every capacity is one")
        return Fraction(entry[field])
    try:
        value, bound = read(answer, "value"), read(answer, "upper_bound")
        flows = [read(f, "flow") for f in answer["flows"]]
        cut = None if answer["cut"] is None else [read(h, "capacity") for h in answer["cut"]] + [
            read(x, "capacity") for x in answer["cut_nodes"]]
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        return [f"max_flow: {exc}"]
    complete = status == COMPLETED
    if answer["optimal"] is not complete or value < 0 or value > bound or (complete and value != bound):
        out.append("the value is at most the upper bound, equal to it and optimal exactly when complete")
    edges = [f["edge"] for f in answer["flows"]]
    if edges != sorted(set(edges)) or any(x <= 0 for x in flows):
        out.append("flows are positive, one per edge, in ascending edge ID order")
    if (cut is not None) != complete or (answer["cut_nodes"] is None) != (answer["cut"] is None):
        out.append("a cut comes exactly with a complete answer")
    elif cut is not None and sum(cut, Fraction(0)) != value:
        out.append("the cut's capacities add up to the value")
    if (answer["certificate"] is not None) != (bool(request["certificate"]) and complete):
        out.append("a certificate comes exactly when asked for, on a complete answer")
    return out


def _matching_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A matching's own consistency, read without the records: pairs by left node, no node in two of them, size their
    number, at most left_count; deficiency and optimality exactly when complete; a certificate (as many nodes as
    pairs) only on a complete answer, exactly when asked for."""
    out = []
    pairs = answer["pairs"]
    lefts = [x["left"] for x in pairs]
    rights = [x["right"] for x in pairs]
    if lefts != sorted(set(lefts)) or len(set(rights)) != len(rights) or set(lefts) & set(rights):
        out.append("pairs are by left node, with no node in two pairs")
    complete = status == COMPLETED
    if answer["size"] != len(pairs) or answer["size"] > answer["left_count"]:
        out.append("size is the number of pairs, at most left_count")
    if answer["optimal"] is not complete or (answer["deficiency"] is None) == complete or (
            complete and answer["deficiency"] != answer["left_count"] - answer["size"]):
        out.append("a complete answer is optimal, its deficiency the left nodes it leaves unpaired")
    cert = answer["certificate"]
    weighted = request["costs"] is not None
    if (cert is not None) != (bool(request["certificate"]) and complete):
        out.append("a certificate comes exactly when asked for, on a complete answer")
    elif cert is not None and (len(cert["nodes"]) != answer["size"] or cert["nodes"] != sorted(set(cert["nodes"]))):
        out.append("the cover has as many nodes as there are pairs, unique and ascending")
    elif cert is not None and cert["kind"] != ("cover_and_potentials" if weighted else "cover"):
        out.append("a cover proves a matching's size; with costs, potentials prove its cost too")
    if weighted != ("cost" in answer):
        out.append("a matching states its cost exactly when the request gives costs")
    elif weighted:
        rational = any(isinstance(x, float) for t in request["costs"].values() for x in t.values())
        if rational != ("cost_exact" in answer):
            out.append("cost_exact appears exactly in rational mode")
        elif rational:
            exact = Fraction(answer["cost_exact"])
            try:
                nearest = float(exact)
            except OverflowError:
                nearest = None
            if str(exact) != answer["cost_exact"] or answer["cost"] != nearest:
                out.append("cost is the nearest float to cost_exact, in lowest terms (null on overflow)")
        elif type(answer["cost"]) is not int:
            out.append("an integer-cost matching costs an integer")
    if cert is not None and weighted:
        names = [e["node"] for e in cert["potentials"]]
        if names != sorted(set(names)):
            out.append("potentials are unique, by node ID")
        rational = any(isinstance(x, float) for t in request["costs"].values() for x in t.values())
        try:                                    # exact numbers only: an integer is an int, never a float
            for x in [e["value"] for e in cert["potentials"]] + [cert["source"], cert["sink"]]:
                _exact_value(x, rational, signed=True)
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            out.append(f"certificate numbers: {exc}")
    return out


def _incidence_problems(request: dict, answer: dict, status: str) -> list[str]:
    """An incidence answer's own consistency, read without the records: nothing claimed when interrupted; when
    complete, a rank no larger than either dimension, each requested kernel with as many vectors as its dimension
    leaves (rows - rank on the left, columns - rank on the right), each vector its nonzero entries by node ID,
    primitive integers; a certificate exactly when asked for, with a minor of rank rows and columns, and a kernel
    (left or right) when the answer lists none. Whether the vectors are in the kernels and the minor nonsingular is checked
    against the records (:func:`graph_runtime.verify_result`)."""
    out = []
    from math import gcd
    if status != COMPLETED:
        if any(answer[k] is not None for k in ("rank", "left_kernel", "right_kernel", "certificate")):
            out.append("an interrupted incidence answer claims nothing: rank, kernels and certificate are null")
        return out
    rank, m, n = answer["rank"], answer["rows"], answer["columns"]
    if type(rank) is not int or not 0 <= rank <= min(m, n):
        out.append("the rank is an integer no larger than either dimension")
        return out

    def vectors(field: str, size: int, vecs: list) -> None:
        if len(vecs) != size:
            out.append(f"{field} has {len(vecs)} vectors, not {size}")
        for vec in vecs:
            names = [e["node"] for e in vec]
            values = [e["value"] for e in vec]
            if not vec or names != sorted(set(names)) or any(type(v) is not int or v == 0 for v in values):
                out.append(f"{field}: a vector is its nonzero integer entries by node ID")
                return
            g = 0
            for v in values:
                g = gcd(g, v)
            if g != 1:
                out.append(f"{field}: a vector's entries have no common factor")
                return
    kernels = request["kernels"]
    if (answer["left_kernel"] is not None) != ("left" in kernels) or (answer["right_kernel"] is not None) != (
            "right" in kernels):
        out.append("a kernel is listed exactly when asked for")
        return out
    if answer["left_kernel"] is not None:
        vectors("left_kernel", m - rank, answer["left_kernel"])
    if answer["right_kernel"] is not None:
        vectors("right_kernel", n - rank, answer["right_kernel"])
    cert = answer["certificate"]
    if (cert is not None) != bool(request["certificate"]):
        out.append("a certificate comes exactly when asked for, on a complete answer")
    elif cert is not None:
        minor = cert["minor"]
        if len(set(minor["rows"])) != rank or len(set(minor["columns"])) != rank:
            out.append("the minor has rank rows and rank columns")
        if (cert["kernel"] is None) != bool(kernels):
            out.append("the certificate carries a kernel exactly when the answer lists none")
        elif cert["kernel"] is not None:
            side = cert["kernel"]["side"]
            vectors("certificate kernel", (m if side == "left" else n) - rank, cert["kernel"]["vectors"])
    return out


def _critical_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A critical path answer's own consistency, read without the records: nothing claimed when interrupted; a cyclic
    answer gives a closed chain of hops (and, with sources, hops from one to it) and nothing else; an acyclic one a
    makespan, a critical path whose hops chain and add up to it, with schedule every node by ID with its earliest and
    latest time and their difference as slack, none negative, and on request the order, every scheduled node once
    with its earliest time. Numbers are integers, or nearest floats with exact p/q beside them when a cost is a float.
    Whether the order and times hold is checked against the records (:func:`graph_runtime.verify_result`)."""
    out = []
    costs = request["costs"]
    rational = costs is not None and any(isinstance(x, float) for x in next(iter(costs.values())).values())

    def read(entry: Mapping, field: str) -> Fraction:
        if rational:
            exact = entry[f"{field}_exact"]
            x = Fraction(exact)
            if str(x) != exact or entry[field] != _nearest(x):
                raise ValueError(f"{field}_exact is a rational in lowest terms, {field} its nearest float")
            return x
        if type(entry[field]) is not int or f"{field}_exact" in entry:
            raise ValueError(f"{field} is an integer, with no {field}_exact, when every cost is one")
        return Fraction(entry[field])
    if status != COMPLETED:
        if any(answer[k] is not None for k in ("acyclic", "cycle", "cost", "path", "schedule", "certificate")):
            out.append("an interrupted critical path answer claims nothing")
        return out
    if answer["acyclic"] is False:
        cyc = answer["cycle"]
        hops = cyc["hops"] if cyc else []
        if not hops or any(a["to"] != b["from"] for a, b in zip(hops, hops[1:])) or hops[-1]["to"] != hops[0]["from"]:
            out.append("a cyclic answer gives a closed chain of hops")
        if cyc and (cyc["reach"] is None) != (request["sources"] is None):
            out.append("a cycle comes with the hops that reach it from a source exactly when sources are named")
        if any(answer[k] is not None for k in ("cost", "path", "schedule", "certificate")):
            out.append("a cyclic answer has no makespan, path, schedule or order")
        return out
    if answer["acyclic"] is not True or answer["cycle"] is not None:
        return ["a complete answer says whether the graph is acyclic, with a cycle only when it is not"]
    try:
        cost = read(answer, "cost")
        path = answer["path"]
        if path is not None:
            hops = path["hops"]
            total = sum((Fraction(h["cost"]) if request["costs"] is not None else Fraction(1) for h in hops),
                        Fraction(0))
            at = path["source"]
            for h in hops:
                if h["from"] != at:
                    out.append("the critical path's hops chain")
                    break
                at = h["to"]
            if at != path["target"] or path["length"] != len(hops) or total != cost:
                out.append("the critical path ends at its target, its length its hops, its cost the makespan")
        sched = answer["schedule"]
        if (sched is not None) != (request["answer"] == "schedule"):
            out.append("a schedule comes exactly when asked for")
        elif sched is not None:
            names = [e["node"] for e in sched]
            if names != sorted(set(names)):
                out.append("the schedule lists every node once, by node ID")
            for e in sched:
                a, b, s = read(e, "earliest"), read(e, "latest"), read(e, "slack")
                if s != b - a or s < 0 or a < 0 or b > cost:
                    out.append(f"{e['node']!r}: slack is latest less earliest, never negative, within the makespan")
                    break
        cert = answer["certificate"]
        if (cert is not None) != bool(request["certificate"]):
            out.append("a certificate comes exactly when asked for")
        elif cert is not None:
            order = [e["node"] for e in cert["order"]]
            values = [read(e, "earliest") for e in cert["order"]]
            if len(set(order)) != len(order) or (values and max(values) != cost):
                out.append("the order lists every scheduled node once, the largest earliest time the makespan")
            if sched is not None and sorted(order) != [e["node"] for e in sched]:
                out.append("the order and the schedule list the same nodes")
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        return [f"critical path: {exc}"]
    return out


def _separation_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A d-separation answer's own consistency, read without the records: nothing claimed when interrupted; when not
    separated, a trail from a node of x to one of y whose steps chain (down from a hop's parent to its child, up the
    other way), every collider on it (in going down, out going up) listed once with a path down to a given node, every
    other inner node not given; when separated, no trail, and a certificate exactly when asked for. Whether the hops
    and paths are the records' is checked against them (:func:`graph_runtime.verify_result`)."""
    out = []
    if status != COMPLETED:
        if any(answer[k] is not None for k in ("separated", "trail", "colliders", "certificate")):
            out.append("an interrupted d-separation answer claims nothing")
        return out
    given = set(request["given"])
    if answer["separated"] is True:
        if answer["trail"] is not None or answer["colliders"] is not None:
            out.append("a separated answer has no trail")
        if (answer["certificate"] is not None) != bool(request["certificate"]):
            out.append("a certificate comes exactly when asked for, on a separated answer")
        return out
    if answer["separated"] is not False or answer["certificate"] is not None:
        return ["a complete answer says whether x and y are separated; a connected one carries no certificate"]
    steps = answer["trail"] or []
    if not steps:
        return ["a connected answer gives its active trail"]
    at = steps[0]["from"] if steps[0]["travel"] == "down" else steps[0]["to"]
    if at not in request["x"]:
        out.append("the trail starts at a node of x")
    expected = []
    for i, s in enumerate(steps):
        here, there = (s["from"], s["to"]) if s["travel"] == "down" else (s["to"], s["from"])
        if here != at:
            out.append("the trail's steps chain")
            break
        if i:
            collider = steps[i - 1]["travel"] == "down" and s["travel"] == "up"
            if collider:
                expected.append(here)
            elif here in given:
                out.append(f"{here!r} is given but not a collider: it blocks the trail")
        at = there
    if at not in request["y"]:
        out.append("the trail ends at a node of y")
    colliders = answer["colliders"] or []
    if [c["node"] for c in colliders] != expected:
        out.append("the colliders are listed once each, in the trail's order")
    for c in colliders:
        path = c["to_given"]
        end = path[-1]["to"] if path else c["node"]
        if end not in given or any(a["to"] != b["from"] for a, b in zip(path, path[1:])) or (
                path and path[0]["from"] != c["node"]):
            out.append(f"collider {c['node']!r}: its path goes down from it to a given node")
    return out


def _acyclic(parent: Mapping) -> bool:
    """Whether every chain of parent links ends (at a node with no parent link), each link followed once: a walk
    stops at a node already settled, and meeting a node of its own walk is a cycle."""
    state: dict = {}
    for start in parent:
        walk, x = [], start
        while x in parent and x not in state:
            state[x] = 1
            walk.append(x)
            x = parent[x]
        if state.get(x) == 1:
            return False
        for y in walk:
            state[y] = 2
    return True


def _dominator_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A dominators answer's own consistency, read without the records: nothing claimed when interrupted; when
    complete, the tree (without targets) by node ID, each node's dominator another listed node, every chain ending at
    a node with none, the sources among those; or (with targets) one chain per target in order, an unreached target
    with no dominators; a certificate exactly when asked for, listing every reached node once. Whether the tree is the
    dominator tree is checked against the records from the certificate (:func:`graph_runtime.verify_result`)."""
    out = []
    if status != COMPLETED:
        if any(answer[k] is not None for k in ("reached", "tree", "chains", "certificate")):
            out.append("an interrupted dominators answer claims nothing: every field is null")
        return out
    if type(answer["reached"]) is not int:
        out.append("a complete answer says how many nodes the sources reach")
    if (answer["tree"] is None) != (request["targets"] is not None) or (answer["chains"] is None) != (
            request["targets"] is None):
        out.append("a tree comes exactly without targets, chains exactly with them")
    tree = answer["tree"]
    if tree is not None:
        names = [e["node"] for e in tree]
        parent = {e["node"]: e["idom"] for e in tree}
        if names != sorted(set(names)) or len(names) != answer["reached"] or any(
                d is not None and (d not in parent or d == n) for n, d in parent.items()):
            out.append("the tree lists each reached node once, by node ID, its dominator another listed node")
        elif any(parent.get(s, 0) is not None for s in request["sources"]):
            out.append("a source has no dominator")
        else:
            if not _acyclic(parent):                         # every chain of dominators ends
                out.append("the dominators form a tree")
    chains = answer["chains"]
    if chains is not None:
        if [c["target"] for c in chains] != request["targets"]:
            out.append("one chain per target, in the request's order")
        for c in chains:
            doms = list(c["dominators"])
            if len(set(doms)) != len(doms) or c["target"] in doms or (not c["reachable"] and doms):
                out.append(f"the chain of {c['target']!r} names each dominator once, not the target, none when "
                           "the target is not reached")
    cert = answer["certificate"]
    if (cert is not None) != bool(request["certificate"]):
        out.append("a certificate comes exactly when asked for, on a complete answer")
    elif cert is not None and len({e["node"] for e in cert["order"]}) != answer["reached"]:
        out.append("the certificate lists every reached node once")
    return out


def _periodicity_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A periodicity answer's own consistency, read without the records: every requested image placed once (all of
    them when complete), components in order of their first seed; per component one tree over its listed nodes, rooted
    where no hop leads, each hop from a listed node's image; a basis in Hermite normal form whose rank and copies it
    states; cycles whose nets the basis generates, and witnesses that write each basis row as a combination of them;
    each seed an image of a listed node offset by a lattice vector; and components over the same nodes that differ by
    a vector outside the lattice. Whether hops, cycles and nets match the records is checked against them
    (:func:`graph_runtime.verify_result`)."""
    from .lattice import coefficients, copies, hermite
    out = []
    comps = answer["components"]
    if answer["count"] != len(comps):
        out.append("count must equal the number of components")
    placed = [s for c in comps for s in c["seeds"]]
    if len(placed) != len(set(placed)) or not set(placed) <= set(request["nodes"]):
        out.append("each requested image is placed in one component at most, and only requested images are")
    if status == COMPLETED and set(placed) != set(request["nodes"]):
        out.append("a complete answer places every requested image")
    if [c["seeds"][0] for c in comps] != sorted(c["seeds"][0] for c in comps):
        out.append("components are in order of their first seed")
    seen: dict = {}
    for n, c in enumerate(comps):
        where = f"component {n}"
        nodes = [x["node"] for x in c["nodes"]]
        if nodes != sorted(set(nodes)) or c["seeds"] != sorted(set(c["seeds"])):
            out.append(f"{where}: nodes and seeds are unique, in ascending ID order")
            continue
        images = {x["node"]: tuple(x["image"]) for x in c["nodes"]}
        dims = len(next(iter(images.values())))
        if any(len(v) != dims for v in images.values()) or any(len(r) != dims for r in c["basis"]):
            out.append(f"{where}: every image and basis row has {dims} components")
            continue
        names = {f"{k}@{','.join(map(str, v))}": k for k, v in images.items()}
        parent = {}
        for x in c["nodes"]:
            if x["via"] is not None:
                parent[x["node"]] = names.get(x["via"]["from"])
        if sum(1 for x in c["nodes"] if x["via"] is None) != 1 or None in parent.values():
            out.append(f"{where}: one listed node is the tree's root, and every hop comes from a listed node's image")
            continue
        if not _acyclic(parent):                            # the tree has no cycle: every chain ends at the root
            out.append(f"{where}: the tree of hops is circular")
        basis = [tuple(r) for r in c["basis"]]
        if hermite(basis, dims) != basis or c["rank"] != len(basis) or c["copies"] != copies(basis):
            out.append(f"{where}: the basis is in Hermite normal form, with the rank and copies it states")
            continue
        net = {}
        for cyc in c["cycles"]:
            if cyc["edge"] in net or len(cyc["net"]) != dims or not any(cyc["net"]) \
                    or coefficients(basis, cyc["net"]) is None:
                out.append(f"{where}: cycle {cyc['edge']!r} is listed once, with a nonzero net in the lattice")
            net[cyc["edge"]] = tuple(cyc["net"])
        if len(c["witnesses"]) != len(basis):
            out.append(f"{where}: one witness per basis row")
        for row, uses in zip(basis, c["witnesses"]):
            total = [0] * dims
            for u in uses:
                if u["edge"] not in net or not u["times"]:
                    out.append(f"{where}: a witness uses listed cycles, each a nonzero number of times")
                    break
                total = [t + u["times"] * x for t, x in zip(total, net[u["edge"]])]
            if tuple(total) != row:
                out.append(f"{where}: the witness of basis row {list(row)} does not add up to it")
        for seed in c["seeds"]:
            node, _, text = seed.rpartition("@")
            parts = text.split(",")
            ok = node in images and len(parts) == dims and all(p.lstrip("-").isdigit() for p in parts)
            if not ok or coefficients(basis, [int(p) - q for p, q in zip(parts, images[node])]) is None:
                out.append(f"{where}: {seed!r} is no image of a listed node by a lattice vector")
        key = tuple(nodes)
        for other in seen.get(key, ()):
            if other[0] == basis and coefficients(basis, [a - b for a, b in zip(images[nodes[0]], other[1])]) \
                    is not None:
                out.append(f"{where}: it is the same component as an earlier one")
        seen.setdefault(key, []).append((basis, images[nodes[0]]))
    return out


class _Values:
    """A derivation objective's values, for checking an answer's arithmetic: ``min_sum`` adds costs (exactly, as
    fractions in rational mode; a given node is worth 0); ``min_max`` and ``max_min`` keep the highest (lowest) cost,
    and a given node is worth None (no hop). On a tie the earlier value is kept, as the engine lists them."""

    def __init__(self, request: dict):
        self.objective = request["objective"]
        costs = request["costs"]
        self.kind, self.table = next(iter(costs.items())) if costs is not None else (None, None)
        self.rational = self.objective == "min_sum" and costs is not None and any(
            isinstance(v, float) for v in self.table.values())
        self.identity = 0 if self.objective == "min_sum" else None

    def cost_of(self, hop: Mapping) -> Any:
        """The cost the request gives a hop (1 when it gives none), or None when it gives none for it."""
        if self.table is None:
            return 1
        return self.table.get(hop["relation"] if self.kind == "by_relation" else hop["edge"])

    def read(self, entry: Mapping, field: str) -> Any:
        """A value as the answer writes it: exact (a fraction from ``_exact`` in rational mode)."""
        if self.rational:
            exact = entry.get(f"{field}_exact")
            if exact is None:
                return None
            number = Fraction(exact)
            if str(number) != exact:
                raise ValueError(f"{field}_exact must be a rational in lowest terms")
            near = _nearest(number)
            if entry.get(field) != near:
                raise ValueError(f"{field} must be the nearest float to {field}_exact")
            return number
        return entry.get(field)

    def hop(self, c: Any, v: Any) -> Any:
        if self.objective == "min_sum":
            return (Fraction(c) if self.rational else c) + v
        if v is None:
            return c
        return v if (v <= c if self.objective == "max_min" else v >= c) else c

    def fold(self, a: Any, b: Any) -> Any:
        if self.objective == "min_sum":
            return a + b
        if a is None or b is None:
            return b if a is None else a
        return a if (a <= b if self.objective == "max_min" else a >= b) else b


def _nearest(x: Fraction) -> float | None:
    try:
        return float(x)
    except OverflowError:
        return None


def _derivation_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A derivation answer's own consistency, read without the records: derived and underived targets (both only when
    complete), a bound exactly when interrupted, a derivation that is closed (every use leads to a listed node),
    acyclic, written in canonical order, whose hops cost what the request gives them and whose every value follows
    from its uses; a certificate exactly when asked for and it says something, agreeing with the derived values.
    Whether the uses are hops of the records, and the certificate's conditions, are checked against the records
    (:func:`graph_runtime.verify_result`)."""
    out = []
    vals = _Values(request)
    targets = set(request["targets"])
    derived, underived = answer["derived"], answer["underived"]
    names = [d["node"] for d in derived]
    if names != sorted(set(names)) or underived != sorted(set(underived)) or set(names) & set(underived):
        out.append("derived and underived targets are unique, in ascending ID order, and never both")
    if not set(names) | set(underived) <= targets:
        out.append("derived and underived nodes are requested targets")
    if status == COMPLETED and set(names) | set(underived) != targets:
        out.append("a complete derivation answer says of every target whether it is derived")
    if status != COMPLETED and underived:
        out.append("only a complete answer certifies a target underived")
    if ("bound" in answer) != (status == INTERRUPTED):
        out.append("a bound appears exactly in an interrupted derivation answer")
    entries = answer["derivation"]
    nodes = [e["node"] for e in entries]
    if nodes != sorted(set(nodes)):
        out.append("derivation entries are unique, in ascending node ID order")
    by = {e["node"]: e for e in entries}
    worth: dict[str, Any] = {}
    state: dict[str, int] = {}                     # 1: being computed (a cycle if met again), 2: done

    def value(n: str) -> Any:
        if state.get(n) == 2:
            return worth[n]
        if state.get(n) == 1:
            raise ValueError(f"the derivation is circular through {n!r}")
        state[n] = 1
        e = by[n]
        acc = vals.identity
        for k, h in enumerate(e["uses"]):
            if h["to"] not in by:
                raise ValueError(f"{n!r} uses {h['to']!r}, which the derivation does not list")
            want = vals.cost_of(h)
            if want is None or h["cost"] != want or type(h["cost"]) is not type(want):
                raise ValueError(f"the hop {h['edge']} must cost what the request gives it")
            x = vals.hop(h["cost"], value(h["to"]))
            acc = x if k == 0 else vals.fold(acc, x)
        worth[n], state[n] = acc, 2
        return acc

    try:
        for e in entries:
            rule, uses = e["rule"], e["uses"]
            if (rule == "given" and uses) or (rule == "one" and len(uses) != 1):
                out.append(f"{e['node']!r}: a given node uses nothing and a one-input node uses one hop")
            if any(h["from"] != e["node"] for h in uses) or len({h["to"] for h in uses}) != len(uses):
                out.append(f"{e['node']!r}: its uses are hops from it, one per input")
            if [(h["direction"] != "forward", h["edge"]) for h in uses] != sorted(
                    (h["direction"] != "forward", h["edge"]) for h in uses):
                out.append(f"{e['node']!r}: its uses are in canonical hop order")
            listed = vals.read(e, "value")
            if value(e["node"]) != listed or (not vals.rational and type(listed) is not type(worth[e["node"]])):
                out.append(f"{e['node']!r}: its value is not what its uses give")
        for d in derived:
            if d["node"] not in by or vals.read(d, "value") != worth.get(d["node"]):
                out.append(f"derived {d['node']!r}: its value is its derivation's")
        bound = vals.read(answer, "bound") if "bound" in answer else None
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        return out + [f"derivation: {exc}"]
    cert = answer["certificate"]
    expected = request["certificate"] and (status == COMPLETED or bound is not None)
    if (cert is not None) != expected:
        return out + ["a certificate comes exactly when asked for, with a complete answer or a known bound"]
    if cert is None:
        return out
    try:
        listed = {c["node"]: vals.read(c, "value") for c in cert["values"]}
        cbound = vals.read(cert, "bound")
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        return out + [f"certificate: {exc}"]
    if [c["node"] for c in cert["values"]] != sorted(listed):
        out.append("certificate values are unique, in ascending node ID order")
    if any(d["node"] not in listed or listed[d["node"]] != worth.get(d["node"]) for d in derived):
        out.append("the certificate gives every derived target its derived value")
    if any(t in listed for t in underived) or (underived and cbound is not None):
        out.append("an underived target is one the certificate leaves unlisted, with no bound")
    if status == INTERRUPTED and cbound != bound:
        out.append("an interrupted answer's certificate bound is its bound")
    return out


def _better(objective: str, a: Any, b: Any) -> bool:
    return a > b if objective == "max_min" else a < b


def _bottleneck_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A bottleneck answer's value is its witness's weakest (or highest) hop weight, each hop weighing what the
    request gives its edge; a bound appears exactly when the search was interrupted, and a value is never better
    than it."""
    out = []
    (kind, table), = request["weights"].items()
    if ("bound" in answer) != (status == INTERRUPTED):
        out.append("a bound appears exactly in an interrupted bottleneck answer")
    if answer["found"] is not True:
        if answer["value"] is not None:
            out.append("only a found witness has a value")
        return out
    weights = []
    for hop in answer["witness"]["hops"]:
        want = table.get(hop["relation"] if kind == "by_relation" else hop["edge"])
        if want is None or hop["weight"] != want or type(hop["weight"]) is not type(want):
            out.append("a hop weight must match the request")
        weights.append(hop["weight"])
    value = (min if request["objective"] == "max_min" else max)(weights) if weights else None
    if answer["value"] != value or type(answer["value"]) is not type(value):
        out.append("the value is the witness's bottleneck: its weakest hop weight (max_min) or its highest (min_max)")
    bound = answer.get("bound")
    if status == INTERRUPTED and bound is not None and value is not None and _better(request["objective"], value, bound):
        out.append("an interrupted answer's value is never better than its bound")
    return out


def _mincost_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A minimum-cost flow's own consistency, read without the records: its numbers written as costs are (exact
    beside the nearest float in rational mode), flows positive and by (edge, direction), each once; feasible and
    optimal known exactly when complete, the value no more than asked and feasible exactly when it is all that was
    asked; a certificate exactly when asked for on a complete answer, with exact potentials, and a cut exactly when
    the flow claims to be a maximum one."""
    out = []
    capacities, costs, value = request["capacities"], request["costs"], request["value"]
    rational = isinstance(value, float) or any(isinstance(x, float) for t in ((capacities or {}), costs)
                                               for table in t.values() for x in table.values())

    def number(owner: dict, field: str) -> Any:
        if rational != (f"{field}_exact" in owner):
            raise ValueError(f"{field}_exact appears exactly in rational mode")
        if not rational:
            if type(owner[field]) is not int or owner[field] < 0:
                raise ValueError(f"{field} is a non-negative integer")
            return owner[field]
        exact = Fraction(owner[f"{field}_exact"])
        try:
            nearest = float(exact)
        except OverflowError:
            nearest = None
        if str(exact) != owner[f"{field}_exact"] or exact < 0 or owner[field] != nearest:
            raise ValueError(f"{field} is the nearest float to {field}_exact, an exact non-negative rational")
        return exact
    complete = status == COMPLETED
    try:
        sent = number(answer, "value")
        number(answer, "cost")
        amounts = [number(f, "flow") for f in answer["flows"]]
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        return [str(exc)]
    keys = [(f["edge"], f["direction"]) for f in answer["flows"]]
    if keys != sorted(set(keys)) or any(a <= 0 for a in amounts):
        out.append("flows are positive, by (edge, direction), each once")
    if answer["optimal"] is not complete or (answer["feasible"] is None) == complete:
        out.append("feasible and optimal are known exactly when the search completed")
    if value is not None and (sent > Fraction(value) or complete and answer["feasible"] is not (sent == Fraction(value))):
        out.append("the value is at most what was asked, and feasible says whether it is all of it")
    if value is None and complete and answer["feasible"] is not True:
        out.append("as much as possible is always feasible")
    cert = answer["certificate"]
    if (cert is not None) != (bool(request["certificate"]) and complete):
        out.append("a certificate comes exactly when asked for, on a complete answer")
    elif cert is not None:
        names = [e["node"] for e in cert["potentials"]]
        if names != sorted(set(names)):
            out.append("potentials are unique, by node ID")
        try:
            for x in [e["value"] for e in cert["potentials"]] + [cert["source"], cert["sink"]]:
                _exact_value(x, rational, signed=True)
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            out.append(f"certificate numbers: {exc}")
        side = cert["source_side"]
        if (side is not None) != (value is None or answer["feasible"] is False):
            out.append("a cut comes exactly when the flow claims to be a maximum one")
        elif side is not None and side != sorted(set(side)):
            out.append("the cut's source side is unique, in ascending ID")
    return out


def _pairs_problems(request: dict, answer: dict, status: str) -> list[str]:
    """The pairs' own consistency, read without the records: the request's pairs in its order (a prefix when
    interrupted), counted, the reachable ones counted; a node reaching itself; a witness exactly for a reachable pair
    when witnesses are asked for, its hops chaining from the source to the target, no node twice; the index's size
    once it is built; the certificate only on a complete answer that asked for it, one closure per source of an
    unreachable pair, in order, each holding its source and none of the targets it cannot reach."""
    out = []
    pairs = answer["pairs"]
    want = [(x["source"], x["target"]) for x in request["pairs"]]
    got = [(x["source"], x["target"]) for x in pairs]
    if got != want[:len(got)] or (status == COMPLETED and len(got) != len(want)):
        out.append("the pairs are the request's, in its order: all of them, or a prefix when interrupted")
    if answer["count"] != len(pairs) or answer["reachable"] != sum(x["reachable"] for x in pairs):
        out.append("count is the number of pairs listed, and reachable the number reachable")
    for n, x in enumerate(pairs):
        if x["source"] == x["target"] and not x["reachable"]:
            out.append(f"pair {n}: a node reaches itself, by the empty walk")
        w = x["witness"]
        if (w is not None) != (x["reachable"] and request["witnesses"]):
            out.append(f"pair {n}: a witness exactly for a reachable pair, when witnesses are asked for")
        if w is None:
            continue
        at, seen = w["source"], [w["source"]]
        for h in w["hops"]:
            if h["from"] != at:
                out.append(f"pair {n}: the witness's hops chain")
            at = h["to"]
            seen.append(at)
        if (w["source"], w["target"]) != (x["source"], x["target"]) or at != w["target"] \
                or w["length"] != len(w["hops"]) or len(set(seen)) != len(seen):
            out.append(f"pair {n}: the witness runs from the source to the target, its length its hops, no node twice")
    index = answer["index"]
    if pairs and index is None:
        out.append("an answer with pairs names its index")
    if index is not None and not (type(index["entries"]) is int and type(index["largest"]) is int
                                  and 0 < index["largest"] <= index["entries"]):
        out.append("the index has entries, its largest pair of labels among them")
    cert = answer["certificate"]
    if cert is not None:
        if status != COMPLETED or not request["certificate"]:
            out.append("a certificate comes only with a complete answer that asked for it")
        sources = [c["source"] for c in cert["closures"]]
        negatives = sorted({x["source"] for x in pairs if not x["reachable"]})
        if sources != negatives:
            out.append("certificate: one closure per source of an unreachable pair, in ascending order")
        closure = {c["source"]: set(c["nodes"]) for c in cert["closures"]}
        for c in cert["closures"]:
            if list(c["nodes"]) != sorted(set(c["nodes"])) or c["source"] not in c["nodes"]:
                out.append(f"certificate: the closure of {c['source']!r} is ascending and holds its source")
        for x in pairs:
            if not x["reachable"] and x["target"] in closure.get(x["source"], ()):
                out.append(f"certificate: {x['target']!r} is in the closure of {x['source']!r}")
    elif status == COMPLETED and request["certificate"]:
        out.append("a complete answer gives the certificate it was asked for")
    return out


def _kpaths_problems(request: dict, answer: dict, status: str) -> list[str]:
    """The k shortest paths' own consistency, read without the records: count their number, at most k; exhausted
    (fewer than k exist) known exactly when complete; each path's length its hops, its hops chaining from its source
    to its target, no node twice, its cost written as costs are (exact beside the nearest float in rational mode)."""
    out = []
    paths = answer["paths"]
    if answer["count"] != len(paths) or len(paths) > request["k"]:
        out.append("count is the number of paths, at most k")
    if (answer["exhausted"] is None) != (status != COMPLETED) or (
            status == COMPLETED and answer["exhausted"] is not (len(paths) < request["k"])):
        out.append("exhausted says, exactly when complete, whether fewer than k paths exist")
    costs = request["costs"]
    rational = costs is not None and any(isinstance(x, float) for t in costs.values() for x in t.values())
    for n, x in enumerate(paths):
        at, seen = x["source"], [x["source"]]
        for h in x["hops"]:
            if h["from"] != at:
                out.append(f"path {n}: its hops chain")
            at = h["to"]
            seen.append(at)
        if at != x["target"] or x["length"] != len(x["hops"]) or len(set(seen)) != len(seen):
            out.append(f"path {n}: its length is its hops, which end at its target and visit no node twice")
        if rational != ("cost_exact" in x):
            out.append(f"path {n}: cost_exact appears exactly in rational mode")
        elif rational:
            exact = Fraction(x["cost_exact"])
            try:
                nearest = float(exact)
            except OverflowError:
                nearest = None
            if str(exact) != x["cost_exact"] or x["cost"] != nearest:
                out.append(f"path {n}: cost is the nearest float to cost_exact (null on overflow)")
        elif type(x["cost"]) is not int:
            out.append(f"path {n}: an integer-cost path costs an integer")
    if out:
        return out
    keys = [((Fraction(x["cost_exact"]) if rational else x["cost"]), x["length"],
             (x["source"],) + tuple(h["edge"] for h in x["hops"])) for x in paths]
    if any(a >= b for a, b in zip(keys, keys[1:])):
        out.append("the paths are distinct and in the order of (cost, hops, start node and edge IDs)")
    return out


def _cycle_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A cycle basis's own consistency, read without the records: the rank known unless interrupted before the
    links were read (then nothing is listed); as many cycles as the rank when complete, no more when not; each cycle
    with its edges unique and ascending, as many nodes as edges and the weight written as costs are; the total
    weight only when complete; witnesses, one per cycle, exactly when asked for and the rank is known."""
    out = []
    rank, cycles = answer["rank"], answer["cycles"]
    weights = request["weights"]
    rational = weights is not None and any(isinstance(x, float) for t in weights.values() for x in t.values())

    def weighed(owner: dict, where: str) -> None:
        if rational != ("weight_exact" in owner):
            out.append(f"{where}: weight_exact appears exactly in rational mode")
        elif rational:
            exact = Fraction(owner["weight_exact"])
            try:
                nearest = float(exact)
            except OverflowError:
                nearest = None
            if str(exact) != owner["weight_exact"] or exact < 0:
                out.append(f"{where}: weight_exact is p or p/q in lowest terms")
            elif owner["weight"] != nearest:
                out.append(f"{where}: weight is the nearest float to weight_exact (null on overflow)")
        elif type(owner["weight"]) is not int or owner["weight"] < 0:
            out.append(f"{where}: an integer-weight cycle weighs a non-negative integer")
    if answer["count"] != len(cycles):
        out.append("count is the number of cycles listed")
    if rank is None:
        if status != INTERRUPTED or cycles or answer["weight"] is not None or answer["certificate"] is not None:
            out.append("an unknown rank belongs to a search interrupted before the links were read, listing nothing")
        return out
    if len(cycles) > rank or status == COMPLETED and len(cycles) != rank:
        out.append("a complete basis lists rank cycles, an interrupted one fewer or as many")
    for n, c in enumerate(cycles):
        if c["edges"] != sorted(set(c["edges"])) or c["length"] != len(c["edges"]) or len(c["nodes"]) != c["length"]:
            out.append(f"cycle {n}: its edges are unique and ascending, its length their number and its nodes as many")
        weighed(c, f"cycle {n}")
    if status == COMPLETED:
        weighed(answer, "the basis")
    elif answer["weight"] is not None or "weight_exact" in answer:
        out.append("an interrupted basis gives no total weight")
    cert = answer["certificate"]
    if (cert is not None) != bool(request["certificate"]):
        out.append("witnesses are given exactly when asked for")
    elif cert is not None and (len(cert["witnesses"]) != len(cycles)
                               or any(w != sorted(set(w)) or not w for w in cert["witnesses"])):
        out.append("one witness per cycle, its edges unique and ascending")
    return out


def _forest_problems(request: dict, answer: dict, status: str) -> list[str]:
    """A spanning forest's own consistency, read without the records: nothing claimed when interrupted; edges unique
    in ascending ID, counted; trees a count; the weight an integer, or in rational mode (a float weight) the nearest
    float beside its exact value."""
    fields = ("edges", "count", "trees", "weight")
    if status != COMPLETED:
        return [] if all(answer[k] is None for k in fields) and "weight_exact" not in answer else \
            ["an interrupted spanning forest claims nothing"]
    out = []
    edges = answer["edges"]
    if edges is None or edges != sorted(set(edges)) or answer["count"] != len(edges):
        out.append("the forest's edges are unique, in ascending ID, and count is their number")
    if type(answer["trees"]) is not int or answer["trees"] < 0:
        out.append("trees is a non-negative integer")
    weights = request["weights"]
    rational = weights is not None and any(isinstance(x, float) for t in weights.values() for x in t.values())
    if rational != ("weight_exact" in answer):
        out.append("weight_exact appears exactly in rational mode (a float weight)")
    elif rational:
        try:
            exact = Fraction(answer["weight_exact"])
        except (ValueError, ZeroDivisionError, TypeError):
            return out + ["weight_exact is p, -p, p/q or -p/q in lowest terms"]
        if str(exact) != answer["weight_exact"]:
            out.append("weight_exact is in lowest terms")
        try:
            nearest = float(exact)
        except OverflowError:
            nearest = None
        if answer["weight"] != nearest:
            out.append("weight is the nearest float to weight_exact (null on overflow)")
    elif type(answer["weight"]) is not int:
        out.append("an integer-weight forest weighs an integer")
    return out


def _pattern_problems(request: dict, answer: dict, status: str, interruption: dict | None) -> list[str]:
    """A pattern answer's own consistency: the rows, the exact total and exists agree with each other, the request's
    answer kind and its limits; every row binds every variable and aligns its links with the steps."""
    out = []
    rows, total, exists = answer["rows"], answer["total"], answer["exists"]
    steps = request["steps"]
    names = [request["match"]["var"]] + [s["var"] for s in steps if "var" in s]
    edge_vars = [s["edge_var"] for s in steps if s["edge_var"] is not None]
    if answer["count"] != len(rows):
        out.append("count must equal the number of rows")
    if exists is None:
        if status != INTERRUPTED or total is not None or rows:
            out.append("an unknown answer (exists null) is an interrupted one, with no total and no rows")
        return out
    if total is None:                       # a cycle's (or injective) rows were being listed when the search stopped
        if status != INTERRUPTED or exists is not True or not (any("to" in s for s in steps)
                                                               or request.get("injective")):
            out.append("an unknown total with rows known to exist is an interrupted answer of a pattern with a cycle "
                       "or an injective one")
        if len(rows) > request["limits"]["max_results"]:
            out.append("more rows than max_results")
    elif type(total) is not int or total < 0 or exists is not (total > 0):
        out.append("total is a non-negative integer and exists says whether it is positive")
        return out
    if total is None:
        return out + _pattern_rows_problems(rows, names, edge_vars, steps, request.get("injective"))
    if request["answer"] != "rows" and rows:
        out.append(f"an answer of {request['answer']} lists no rows")
    if request["answer"] == "rows":
        if status == COMPLETED and len(rows) != total:
            out.append("a complete answer lists every row")
        if status == INTERRUPTED and (interruption or {}).get("reason") == "output_limit" \
                and not len(rows) == request["limits"]["max_results"] < total:
            out.append("an output-limited answer lists max_results rows of a larger total")
    if len(rows) > total:
        out.append("more rows than the total")
    return out + _pattern_rows_problems(rows, names, edge_vars, steps, request.get("injective"))


def _pattern_rows_problems(rows: list, names: list, edge_vars: list, steps: list, injective: bool = False) -> list[str]:
    """Each row binds every variable, every edge variable and one link per step, without repeating another row; a
    cycle's closing step always has its link; an injective pattern's row binds distinct nodes and links."""
    out = []
    seen = set()
    for n, row in enumerate(rows):
        if set(row["nodes"]) != set(names) or set(row["edges"]) != set(edge_vars) or len(row["links"]) != len(steps):
            out.append(f"row {n} binds every variable, every edge variable, and one link per step")
            continue
        if injective:
            bound = [v for v in row["nodes"].values() if v is not None]
            links = [x for x in row["links"] if x is not None]
            if len(set(bound)) != len(bound) or len(set(links)) != len(links):
                out.append(f"row {n} of an injective pattern binds distinct nodes and distinct links")
        if row["nodes"][names[0]] is None:
            out.append(f"row {n} binds the matched variable")
        for j, s in enumerate(steps):
            unbound = row["links"][j] is None
            if "to" in s:
                if unbound:
                    out.append(f"row {n}: step {j} closes a cycle and always has its link")
            elif unbound != (row["nodes"][s["var"]] is None) or (unbound and not s["optional"]):
                out.append(f"row {n}: step {j} binds its variable exactly when it has a link, and only an optional "
                           "step goes unbound")
            if s["edge_var"] is not None and row["edges"][s["edge_var"]] != row["links"][j]:
                out.append(f"row {n}: {s['edge_var']} names step {j}'s link")
        key = canonical_json([row["nodes"], row["links"]])
        if key in seen:
            out.append(f"row {n} repeats an earlier row")
        seen.add(key)
    return out


def _support_problems(request: dict, answer: dict, status: str, interruption: dict | None) -> list[str]:
    """A support answer's own consistency: lost and weakened nodes are listed exactly when both fixpoints were
    reached, completely unless an output limit cut the list (lost nodes first), and each entry's counts, failing
    supports and causes agree with each other and with the request."""
    out = []
    if answer["failed"] != request["failed"]:
        out.append("failed must match the request")
    lost, weakened, counts, cert = answer["lost"], answer["weakened"], answer["counts"], answer["certificate"]
    failed = set(request["failed"])
    if cert is not None:
        if status != COMPLETED:
            out.append("only a complete support answer carries a certificate")
        if not request["certificate"]:
            out.append("a support answer carries a certificate only when the request asks for one")
        names = [e["node"] for e in cert["nodes"]]
        if names != sorted(set(names)):
            out.append("a support certificate lists each node once, in ascending ID order")
        if any(e["after"] is not None for e in cert["nodes"] if e["node"] in failed):
            out.append("a support certificate ranks no failed node as standing after the failure")
    elif request["certificate"] and status == COMPLETED:
        out.append("a complete support answer carries the certificate its request asks for")
    if lost is None or weakened is None or counts is None:
        if lost is not None or weakened is not None or counts is not None or status != INTERRUPTED \
                or (interruption or {}).get("reason") == "output_limit":
            out.append("an unknown support answer (lost, weakened and counts null) is one interrupted before its end")
        return out
    listed, total = len(lost) + len(weakened), counts["lost"] + counts["weakened"]
    cap = request["limits"]["max_results"]
    if status == COMPLETED and (len(lost) != counts["lost"] or len(weakened) != counts["weakened"]):
        out.append("a complete support answer lists every lost and weakened node")
    if status == INTERRUPTED and not ((interruption or {}).get("reason") == "output_limit" and listed == cap < total
                                      and len(lost) == min(cap, counts["lost"])):
        out.append("an interrupted support answer that lists nodes was cut by its output limit: max_results nodes, "
                   "lost ones first, of a larger total")
    if len(lost) > counts["lost"] or len(weakened) > counts["weakened"]:
        out.append("more nodes listed than counted")
    names = [e["node"] for e in lost] + [e["node"] for e in weakened]
    if [e["node"] for e in lost] != sorted({e["node"] for e in lost}) \
            or [e["node"] for e in weakened] != sorted({e["node"] for e in weakened}) or len(set(names)) != len(names):
        out.append("lost and weakened nodes are unique, in ascending ID order, and never both")
    directions = {e["direction"] for e in request["scope"]}
    for kind, entries in (("lost", lost), ("weakened", weakened)):
        for e in entries:
            where = f"{kind} {e['node']!r}"
            if e["node"] in failed:
                out.append(f"{where}: a failed node is neither lost nor weakened")
            supports, standing, failing = e["supports"], e["standing"], e["failing"]
            if not 0 <= standing <= supports - len(failing) or not failing:
                out.append(f"{where}: it lost at least one support, and standing and failing supports are distinct")
            if kind == "weakened" and standing < 1:
                out.append(f"{where}: a weakened node still stands on a support")
            if kind == "lost" and standing >= supports:
                out.append(f"{where}: a lost node has a support that does not stand")
            if any(h["from"] != e["node"] or h["direction"] not in directions for h in failing) \
                    or len({h["to"] for h in failing}) != len(failing):
                out.append(f"{where}: its failing supports are hops in scope from it, one per support")
            if not e["causes"] or e["causes"] != sorted(set(e["causes"])) or not set(e["causes"]) <= failed:
                out.append(f"{where}: its causes are failed nodes, at least one, in ascending ID order")
    return out


def _exact_value(value: Any, rational: bool, signed: bool = False) -> Any:
    """A certificate's number: an integer in integer-cost mode, ``p`` or ``p/q`` in lowest terms in rational mode;
    never negative unless ``signed``."""
    if not rational:
        if type(value) is not int or value < 0 and not signed:
            raise ValueError(f"{value!r} is not a{'n' if signed else ' non-negative'} integer")
        return value
    number = Fraction(value)
    if not isinstance(value, str) or str(number) != value or number < 0 and not signed:
        raise ValueError(f"{value!r} is not an exact{'' if signed else ' non-negative'} rational in lowest terms")
    return number


def _certificate_problems(op: str, request: dict, answer: dict, status: str) -> list[str]:
    """A certificate appears exactly when the request asks for one and the answer has something to prove: a complete
    negative, a proven optimum, or an interrupted search's lower bound. Its form is canonical. Whether it proves the
    answer is checked against the records (:func:`graph_runtime.verify_result`)."""
    out = []
    if "certificate" not in answer:
        return ["a typed or shortest path answer carries its certificate (null when there is none)"]
    cert, found = answer["certificate"], answer["found"]
    if op == "typed_path":
        kind = "closed_states" if request["certificate"] and status == COMPLETED and found is False else None
    elif not request["certificate"]:
        kind = None
    elif op == "bottleneck_path":
        if status == COMPLETED:
            kind = "closure" if not found else ("threshold" if answer["value"] is not None else None)
        else:
            kind = "threshold" if answer.get("bound") is not None else None
    elif "negative_cycle" in answer:                       # a negative cost: potentials of any sign, or nothing
        kind = (None if status != COMPLETED or answer["negative_cycle"] is not None
                else "feasible_potentials" if found else "closure")
    elif status == COMPLETED:
        kind = "potentials" if found else "closure"
    else:
        (_, table), = request["costs"].items()
        rational = any(isinstance(v, float) for v in table.values())
        kind = "potentials" if answer.get("lower_bound_exact" if rational else "lower_bound") is not None else None
    if (cert is None) != (kind is None) or (cert is not None and cert["kind"] != kind):
        return [f"the certificate is {kind or 'null'} for this request and answer, not "
                f"{None if cert is None else cert['kind']}"]
    if cert is None:
        return out
    sources = set(request["sources"])
    targets = None if request.get("targets") is None else set(request["targets"])
    if kind == "closed_states":
        states = [(st["position"], st["node"]) for st in cert["states"]]
        if states != sorted(set(states)) or any(type(i) is not int for i, _ in states):
            out.append("certificate states are unique, by position then node ID")
        if any(not 0 <= i < len(request["pattern"]) for i, _ in states):
            out.append("certificate states are at positions before the pattern's end")
        if not {(0, s) for s in sources} <= set(states):
            out.append("certificate states start with every source at position 0")
        return out
    if kind in ("closure", "threshold"):
        reached = cert["reached"]
        if reached != sorted(set(reached)) or not sources <= set(reached) or targets & set(reached):
            out.append(f"a {kind} certificate lists, in ascending ID, what the sources reach: every source and no "
                       "target")
        if kind == "threshold" and cert["threshold"] != (answer["value"] if status == COMPLETED else answer["bound"]):
            out.append("a threshold certificate's threshold is the answer's value (or, interrupted, its bound)")
        return out
    (_, table), = request["costs"].items()
    rational = any(isinstance(v, float) for v in table.values())
    signed = kind == "feasible_potentials"
    try:
        bound = _exact_value(cert["bound"], rational, signed)
        values = [(e["node"], _exact_value(e["value"], rational, signed)) for e in cert["potentials"]]
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        return [f"certificate numbers: {exc}"]
    claimed = answer["cost_exact" if status == COMPLETED else "lower_bound_exact"] if rational else \
        answer["cost" if status == COMPLETED else "lower_bound"]
    if bound != (Fraction(claimed) if rational else claimed):
        out.append("a potentials certificate bounds the answer's cost (or its lower bound)")
    names = [n for n, _ in values]
    if signed:
        dead = cert["dead_ends"]
        if names != sorted(set(names)) or dead != sorted(set(dead)) or set(names) & set(dead):
            out.append("feasible potentials and dead ends are unique, each in ascending node ID, and apart")
        at = dict(values)
        if any(s not in at and s not in dead or at.get(s, 0) > 0 for s in sources) \
                or any(t in dead or at.get(t, bound) < bound for t in targets):
            out.append("feasible potentials give every source 0 or less (or call it a dead end), and every target "
                       "the bound or more (never a dead end)")
        return out
    if names != sorted(set(names)) or any(not 0 <= v < bound for _, v in values):
        out.append("potentials are unique, in ascending node ID, each at least 0 and below the bound")
    at = dict(values)
    if any(at.get(s, bound) != 0 for s in sources) or any(t in at for t in targets):
        out.append("potentials give every source 0 and no target less than the bound")
    return out


def _cost_problems(request: dict, answer: dict, status: str) -> list[str]:
    out = []
    (kind, table), = request["costs"].items()
    rational = any(isinstance(v, float) for v in table.values())

    def number(field, owner=None):
        owner = answer if owner is None else owner
        value = owner[field]
        if not rational:
            if type(value) is not int or f"{field}_exact" in owner:
                raise ValueError(f"{field} must be an integer in integer-cost mode")
            return value
        exact = owner[f"{field}_exact"]
        number = Fraction(exact)
        if str(number) != exact:
            raise ValueError(f"{field}_exact must be a rational in lowest terms")
        try:
            nearest = float(number)
        except OverflowError:
            nearest = None
        if value != nearest:
            out.append(f"{field} must be the nearest float to {field}_exact (null on overflow)")
        return number

    def summed(hops: list) -> Any:
        total = 0
        for hop in hops:
            if hop["direction"] not in {s["direction"] for s in request["scope"]}:
                out.append("a shortest path hop goes in a direction the scope does not follow")
            expected = table.get(hop["relation"] if kind == "by_relation" else hop["edge"])
            if expected is None or hop["cost"] != expected or type(hop["cost"]) is not type(expected):
                out.append("a hop cost must match the request")
            total += Fraction(hop["cost"]) if rational else hop["cost"]
        return total

    signed = any(v < 0 for v in table.values())
    if signed != ("negative_cycle" in answer):
        return out + ["an answer gives negative_cycle exactly when the request gives a negative cost"]
    cycle = answer.get("negative_cycle")
    if cycle is not None:
        if status != COMPLETED or answer["found"] is not True or answer["cost"] is not None \
                or answer["witness"] is not None or "cost_exact" in answer:
            out.append("a negative cycle is a complete, found answer with no cost and no witness")
        summed(cycle["lead_in"]["hops"])
        summed(cycle["lead_out"]["hops"])
        loop = cycle["cycle"]
        total = summed(loop["hops"])
        if total != number("cost", loop) or not total < 0:
            out.append("the negative cycle's cost is the exact sum of its hops' costs, and negative")
        return out
    if signed and status == INTERRUPTED and (answer.get("lower_bound") is not None or "lower_bound_exact" in answer):
        out.append("with a negative cost, an interrupted answer gives no lower bound")
    lower = number("lower_bound") if status == INTERRUPTED and not signed else None
    if answer["found"] is not True:
        return out
    cost = number("cost")
    if lower is not None and lower > cost:
        out.append("lower_bound exceeds the witness cost")
    if summed(answer["witness"]["hops"]) != cost:
        out.append("the witness cost must equal the exact sum of hop costs")
    return out
