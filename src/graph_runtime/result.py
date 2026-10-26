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
        for k in parent:                                    # the tree has no cycle: every chain ends at the root
            at, steps = k, 0
            while at in parent and steps <= len(parent):
                at, steps = parent[at], steps + 1
            if at in parent:
                out.append(f"{where}: the tree of hops is circular")
                break
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
    if total is None:                                   # a cycle's rows were being listed when the search stopped
        if status != INTERRUPTED or exists is not True or not any("to" in s for s in steps):
            out.append("an unknown total with rows known to exist is an interrupted answer of a pattern with a cycle")
        if len(rows) > request["limits"]["max_results"]:
            out.append("more rows than max_results")
    elif type(total) is not int or total < 0 or exists is not (total > 0):
        out.append("total is a non-negative integer and exists says whether it is positive")
        return out
    if total is None:
        return out + _pattern_rows_problems(rows, names, edge_vars, steps)
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
    return out + _pattern_rows_problems(rows, names, edge_vars, steps)


def _pattern_rows_problems(rows: list, names: list, edge_vars: list, steps: list) -> list[str]:
    """Each row binds every variable, every edge variable and one link per step, without repeating another row; a
    cycle's closing step always has its link."""
    out = []
    seen = set()
    for n, row in enumerate(rows):
        if set(row["nodes"]) != set(names) or set(row["edges"]) != set(edge_vars) or len(row["links"]) != len(steps):
            out.append(f"row {n} binds every variable, every edge variable, and one link per step")
            continue
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
    lost, weakened, counts = answer["lost"], answer["weakened"], answer["counts"]
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
    failed = set(request["failed"])
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


def _exact_value(value: Any, rational: bool) -> Any:
    """A certificate's number: an integer in integer-cost mode, ``p`` or ``p/q`` in lowest terms in rational mode."""
    if not rational:
        if type(value) is not int or value < 0:
            raise ValueError(f"{value!r} is not a non-negative integer")
        return value
    number = Fraction(value)
    if not isinstance(value, str) or str(number) != value or number < 0:
        raise ValueError(f"{value!r} is not an exact non-negative rational in lowest terms")
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
    try:
        bound = _exact_value(cert["bound"], rational)
        values = [(e["node"], _exact_value(e["value"], rational)) for e in cert["potentials"]]
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        return [f"certificate numbers: {exc}"]
    claimed = answer["cost_exact" if status == COMPLETED else "lower_bound_exact"] if rational else \
        answer["cost" if status == COMPLETED else "lower_bound"]
    if bound != (Fraction(claimed) if rational else claimed):
        out.append("a potentials certificate bounds the answer's cost (or its lower bound)")
    names = [n for n, _ in values]
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
        if hop["direction"] not in {s["direction"] for s in request["scope"]}:
            out.append("a shortest path hop goes in a direction the scope does not follow")
        expected = table.get(hop["relation"] if kind == "by_relation" else hop["edge"])
        if expected is None or hop["cost"] != expected or type(hop["cost"]) is not type(expected):
            out.append("a hop cost must match the request")
        total += Fraction(hop["cost"]) if rational else hop["cost"]
    if total != cost:
        out.append("the witness cost must equal the exact sum of hop costs")
    return out
