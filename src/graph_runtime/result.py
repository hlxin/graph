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
from typing import Any

from ._canonical import canonical_json, digest, freeze, thaw
from ._version import RUNTIME_NAME, __version__
from .projection import CONTRACT

COMPLETED, INTERRUPTED, UNSUPPORTED, FAILED, STALE = (
    "completed", "interrupted", "unsupported", "failed", "stale_snapshot")
STATUSES = (COMPLETED, INTERRUPTED, UNSUPPORTED, FAILED, STALE)
INTERRUPTIONS = ("expansion_budget", "output_limit", "deadline", "cancelled")
RESULT_KIND = "graph_runtime.result"
_SET_OPERATIONS = {"reachability": "reached", "dependency_impact": "affected"}
_WITNESS_OPERATIONS = ("typed_path", "shortest_path")


class Result:
    """A read-only result. :meth:`to_dict` gives its JSON form; :meth:`from_dict` reads one back and checks its
    ``result_id``."""

    __slots__ = ("_data", "_timing", "_index")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("results are made by graph_runtime.execute(...) or read with Result.from_dict(...)")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("a Result is immutable")

    @classmethod
    def _create(cls, *, status: str, operation: str | None = None, request: Any = None, projection: str | None = None,
                answer: Mapping | None = None, interruption: Mapping | None = None, error: Mapping | None = None,
                stats: Mapping | None = None, limitations: list[str] | tuple[str, ...] = (),
                timing: Mapping | None = None, started: float | None = None) -> "Result":
        """``timing``: the seconds each phase took; ``started`` (a ``time.perf_counter()`` value): when the execution
        began, so that ``total_s`` covers making the result too."""
        made = time.perf_counter()
        if status not in STATUSES:
            raise ValueError(f"status {status!r} is not one of {STATUSES}")
        data = {"kind": RESULT_KIND, "contract": CONTRACT,
                "runtime": {"name": RUNTIME_NAME, "version": __version__},
                "status": status, "complete": status == COMPLETED, "operation": operation,
                "mode": request["mode"] if request is not None else None, "projection": projection,
                "request": request.to_dict() if request is not None else None,
                "request_id": request.request_id if request is not None else None,
                "answer": answer, "interruption": interruption, "error": error,
                "stats": dict(stats or {"expansions": 0, "edges_examined": 0, "discovered": 0}),
                "limitations": list(limitations)}
        data["result_id"] = digest(data)
        r = cls._wrap(data, None)
        if timing or started is not None:
            done = time.perf_counter()
            t = {**(timing or {}), "result_s": done - made}
            if started is not None:
                t["total_s"] = done - started
            object.__setattr__(r, "_timing", freeze(t))
        return r

    @classmethod
    def _wrap(cls, data: dict, timing: Mapping | None) -> "Result":
        r = object.__new__(cls)
        object.__setattr__(r, "_data", freeze(data))
        object.__setattr__(r, "_timing", freeze(dict(timing or {})))
        object.__setattr__(r, "_index", None)
        return r

    @classmethod
    def from_dict(cls, data: Mapping) -> "Result":
        """Read a result's JSON form; its ``result_id`` must match its content (``timing`` aside)."""
        if not isinstance(data, Mapping) or data.get("kind") != RESULT_KIND:
            raise ValueError(f"a result has kind {RESULT_KIND!r}")
        body = {k: thaw(v) for k, v in data.items() if k not in ("result_id", "timing")}
        if data.get("result_id") != digest(body):
            raise ValueError("the result's content does not match its result_id")
        return cls._wrap({**body, "result_id": data["result_id"]}, data.get("timing"))

    # ------------------------------------------------------------------------------------------------ fields
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
        return self._data["request"]

    @property
    def request_id(self) -> str | None:
        return self._data["request_id"]

    @property
    def answer(self) -> Mapping | None:
        return self._data["answer"]

    @property
    def interruption(self) -> Mapping | None:
        return self._data["interruption"]

    @property
    def error(self) -> Mapping | None:
        return self._data["error"]

    @property
    def stats(self) -> Mapping:
        return self._data["stats"]

    @property
    def limitations(self) -> tuple[str, ...]:
        return self._data["limitations"]

    @property
    def result_id(self) -> str:
        return self._data["result_id"]

    @property
    def timing(self) -> Mapping:
        """Wall-clock seconds by phase; not part of ``result_id``."""
        return self._timing

    def to_dict(self, *, timing: bool = True) -> dict:
        out = thaw(self._data)
        if timing:
            out["timing"] = thaw(self._timing)
        return out

    def to_json(self, *, timing: bool = False, indent: int | None = None) -> str:
        """The JSON form; canonical (sorted keys, compact) unless ``indent`` is given."""
        if indent is None:
            return canonical_json(self.to_dict(timing=timing))
        import json
        return json.dumps(self.to_dict(timing=timing), indent=indent, sort_keys=True, ensure_ascii=False)

    def __repr__(self) -> str:
        return f"<Result {self.operation} {self.status} complete={self.complete}>"

    # ------------------------------------------------------------------------------------------------ reading answers
    def _entries(self) -> dict[str, Mapping]:
        key = _SET_OPERATIONS.get(self.operation or "")
        if key is None:
            raise ValueError(f"a {self.operation} result lists no node set")
        if self._index is None:
            items = (self.answer or {}).get(key) or ()
            object.__setattr__(self, "_index", {e["node"]: e for e in items})
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
        return False if self.complete else None

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
        return None if self.answer is None else self.answer["found"]

    @property
    def witness(self) -> list[dict] | None:
        """For typed and shortest paths: the witness hops, or None."""
        if self.operation not in _WITNESS_OPERATIONS:
            raise ValueError(f"witness reads a typed_path or shortest_path result, not {self.operation}")
        w = (self.answer or {}).get("witness")
        return None if w is None else thaw(w["hops"])
