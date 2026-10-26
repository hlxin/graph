"""The reference CPU engine: binds a request to a projection and runs the exact operations under their limits.

Order (what makes answers reproducible and decides among equally good witnesses): nodes and edges in ascending
original ID; a node's hops are its forward-scope edges (it is their source) in ascending edge ID, then its
reverse-scope edges (it is their target) in ascending edge ID; the breadth-first searches run first in, first out
from the sources in ascending ID; the shortest-path search settles nodes by (cost, node ID), and a source that is
also a target is the answer at cost 0. Which of several equally good witnesses an answer gives follows from this
order within a contract version; a consumer must not read meaning into the choice.

Limits: ``max_expansions`` (a request limit) bounds the search states expanded; ``max_results`` (a request limit)
bounds the nodes a reachability or impact answer lists; a deadline and a cancellation token (execution controls,
not part of the request) are checked before the first expansion and every ``check_interval`` expansions after it.
Whichever stops the search first makes the result ``interrupted``: what it lists stays valid, and nothing is
certified absent. A search that needs no further expansion completes, whatever the limits.
"""

from __future__ import annotations

import heapq
import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from fractions import Fraction
from types import SimpleNamespace
from typing import Any

from ._canonical import json_safe
from .errors import RequestError
from .projection import FORWARD, REVERSE, Projection
from .request import FAILED, Request, normalize_request
from .result import COMPLETED, INTERRUPTED, STALE, Result

_DIRECTION = (FORWARD, REVERSE)          # the flag a hop carries: 0 forward, 1 reverse

SCOPE_NOTE = ("Complete for this request on projection {projection}: an absence here is an absence in this "
              "projection, not in the literature or the physical world.")
INCOMPLETE_NOTE = ("Incomplete: interrupted ({reason}) after {expansions} expansions. What is listed is valid, each "
                   "with its witness; nothing is certified absent.")
WITNESS_NOTE = ("A witness shows a connection in this projection only; what it means scientifically is for the "
                "consuming application to assess.")
TENTATIVE_NOTE = ("The witness is a valid path, but its cost is not proven minimal: the minimum cost lies between "
                  "lower_bound and cost.")
NO_ANSWER_NOTE = "No answer: the request was not run ({code})."
CERTIFIED_NOTE = ("The certificate proves the answer's negative or bound against the projection's records without a "
                  "new search: verify_result checks it, and revalidate checks it against an edited projection.")


class CancelToken:
    """A cancellation flag that a caller sets from any thread; :func:`execute` reads it between expansions."""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


def _cancel_check(cancel: Any) -> Callable[[], bool] | None:
    if cancel is None:
        return None
    if isinstance(cancel, CancelToken):
        return lambda: cancel.cancelled
    if callable(getattr(cancel, "is_set", None)):          # a threading.Event
        return cancel.is_set
    if callable(cancel):
        return lambda: bool(cancel())
    raise TypeError("cancel is a CancelToken, a threading.Event or a callable that returns True once cancelled")


class _Control:
    """One execution's limits: the expansion budget, a deadline and a cancellation, read before every expansion."""

    __slots__ = ("budget", "expansions", "deadline", "clock", "cancelled", "interval", "countdown")

    def __init__(self, budget: int, deadline: float | None, clock: Callable[[], float],
                 cancelled: Callable[[], bool] | None, interval: int):
        self.budget, self.expansions = budget, 0
        self.deadline, self.clock, self.cancelled = deadline, clock, cancelled
        self.interval, self.countdown = interval, 1          # the first expansion checks the clock and the token

    def stop(self) -> str | None:
        """Why the next expansion must not happen, or None (and it is counted)."""
        if self.expansions >= self.budget:
            return "expansion_budget"
        self.countdown -= 1
        if self.countdown <= 0:
            self.countdown = self.interval
            if self.cancelled is not None and self.cancelled():
                return "cancelled"
            if self.deadline is not None and self.clock() >= self.deadline:
                return "deadline"
        self.expansions += 1
        return None


def execute(projection: Projection, request: Mapping | Request, *, timeout: float | None = None,
            deadline: float | None = None, cancel: Any = None, current_bindings: Mapping[str, str] | None = None,
            clock: Callable[[], float] = time.monotonic, check_interval: int = 64) -> Result:
    """Run ``request`` on ``projection`` and return its :class:`Result`.

    A problem with the request never raises: the result says it (``failed``, ``unsupported`` or ``stale_snapshot``).
    ``timeout`` (seconds from now) or ``deadline`` (a ``clock()`` time) and ``cancel`` (a :class:`CancelToken`, a
    ``threading.Event`` or a callable) may interrupt the search. ``current_bindings``: what the consumer's source,
    schema and policy are now; when they differ from the projection's bindings the result is ``stale_snapshot``."""
    started = time.perf_counter()
    if not isinstance(projection, Projection):
        raise TypeError(f"execute runs on a Projection, not {type(projection).__name__}")
    if timeout is not None and deadline is not None:
        raise TypeError("give a timeout or a deadline, not both")
    if type(check_interval) is not int or check_interval < 1:
        raise ValueError("check_interval is a positive integer")
    for name, value in (("timeout", timeout), ("deadline", deadline)):
        if value is not None and (type(value) not in (int, float) or
                                  (isinstance(value, float) and not math.isfinite(value))):
            raise ValueError(f"{name} is a finite number of seconds")
    if not callable(clock):
        raise TypeError("clock is callable")
    cancelled = _cancel_check(cancel)
    try:
        if timeout is not None:
            deadline = clock() + timeout
        return _execute(projection, request, deadline, clock, cancelled, check_interval, current_bindings, started)
    except Exception as exc:  # noqa: BLE001 - the contract reports a runtime failure as a result, never raises it
        return _no_answer(FAILED, "internal_error", f"{type(exc).__name__}: {exc}", {"exception": type(exc).__name__},
                          projection=projection.identity, started=started)


def _execute(projection: Projection, request: Any, deadline: float | None, clock: Callable[[], float],
             cancelled: Callable[[], bool] | None, check_interval: int, current_bindings: Mapping[str, str] | None,
             started: float) -> Result:
    try:
        req = normalize_request(request)
    except RequestError as e:
        op = request.get("operation") if isinstance(request, Mapping) else None
        return _no_answer(e.status, e.code, e.message, e.detail, operation=op if isinstance(op, str) else None,
                          projection=projection.identity, started=started)
    if req.projection != projection.identity:
        return _no_answer(STALE, "projection_mismatch", f"the request was made for projection {req.projection}, not "
                          f"{projection.identity}", {"expected": req.projection, "actual": projection.identity},
                          request=req, projection=projection.identity, started=started)
    if current_bindings is not None:
        changed = projection.stale_bindings(current_bindings)
        if changed:
            return _no_answer(STALE, "bindings_changed", f"the bindings {list(changed)} no longer match what the "
                              "projection was built from; build a new projection", {"bindings": list(changed)},
                              request=req, projection=projection.identity, started=started)
    try:
        plan = _bind(projection, req)
    except RequestError as e:
        return _no_answer(e.status, e.code, e.message, e.detail, request=req, projection=projection.identity,
                          started=started)

    bound = time.perf_counter()
    ctl = _Control(req.limits["max_expansions"], deadline, clock, cancelled, check_interval)
    search, build = _OPERATIONS[req.operation]
    try:
        state = search(projection, plan, ctl)
        searched = time.perf_counter()
        answer, extra = build(projection, plan, state)
    except Exception as exc:  # noqa: BLE001 - the contract reports a runtime failure as a result
        return _no_answer(FAILED, "internal_error", f"{type(exc).__name__}: {exc}", {"exception": type(exc).__name__},
                          request=req, projection=projection.identity, started=started)
    built = time.perf_counter()

    why = state.interruption
    interruption = None
    if why:
        limit = {"expansion_budget": req.limits["max_expansions"],
                 "output_limit": req.limits.get("max_results")}.get(why)
        interruption = {"reason": why, "expansions": ctl.expansions, "limit": limit}
        notes = [INCOMPLETE_NOTE.format(reason=why, expansions=ctl.expansions)]
    else:
        notes = [SCOPE_NOTE.format(projection=projection.identity)]
    notes += extra
    stats = {"expansions": ctl.expansions, "edges_examined": state.examined, "discovered": state.discovered}
    try:
        return Result._create(status=INTERRUPTED if why else COMPLETED, operation=req.operation, request=req,
                              projection=projection.identity, answer=answer, interruption=interruption, stats=stats,
                              limitations=notes, started=started,
                              timing={"bind_s": bound - started, "search_s": searched - bound,
                                      "witness_s": built - searched})
    except (ValueError, TypeError) as exc:        # a number too long for JSON, say: the answer exists but cannot travel
        return _no_answer(FAILED, "unrepresentable_result", f"the answer cannot be written as JSON: {exc}", None,
                          request=req, projection=projection.identity, started=started)


def _no_answer(status: str, code: str, message: str, detail: Mapping | None, *, request: Request | None = None,
               operation: Any = None, projection: str | None = None, started: float | None = None) -> Result:
    """A result without an answer. What it quotes of the request is made safe for JSON first (:func:`json_safe`), so
    that a request with unencodable text or an enormous number can still be refused in a result."""
    error = {"code": code, "message": json_safe(message), **({"detail": json_safe(dict(detail))} if detail else {})}
    operation = json_safe(operation) if isinstance(operation, str) else None
    return Result._create(status=status, operation=request.operation if request is not None else operation,
                          request=request, projection=projection, error=error,
                          limitations=[NO_ANSWER_NOTE.format(code=code)], started=started)


# ---------------------------------------------------------------------------------------------- binding
def _bind(p: Projection, req: Request) -> SimpleNamespace:
    """The request checked against the projection (every node, relation, type and edge it names exists; the costs
    cover every edge in scope) and compiled to integer form."""
    op = req.operation
    plan = SimpleNamespace(max_results=req.limits.get("max_results"))
    if op in ("reachability", "dependency_impact"):
        field = "changed" if op == "dependency_impact" else "sources"
        plan.sources = _nodes(p, req[field], field)
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        plan.max_depth = req["max_depth"]
    elif op == "typed_path":
        plan.certificate = req["certificate"]
        plan.sources = _nodes(p, req["sources"], "sources")
        plan.targets = None if req["targets"] is None else set(_nodes(p, req["targets"], "targets"))
        plan.positions = []
        for i, pos in enumerate(req["pattern"]):
            fwd, rev = _scope(p, pos["scope"], f"pattern[{i}].scope")
            mask = None if pos["node_types"] is None else _types(p, pos["node_types"], f"pattern[{i}].node_types")
            plan.positions.append((fwd, rev, mask))
    elif op == "pattern":
        m = req["match"]
        names = [m["var"]] + [st["var"] for st in req["steps"]]
        index = {name: i for i, name in enumerate(names)}
        plan.vars, plan.answer = names, req["answer"]
        plan.match = _node_filter(p, m, "match")
        plan.steps = []
        for i, st in enumerate(req["steps"]):
            fwd, rev = _scope(p, st["scope"], f"steps[{i}].scope")
            tmask, nodes = _node_filter(p, st, f"steps[{i}]")
            edges = None
            if st["edges"] is not None:
                unknown = [e for e in st["edges"] if e not in p._edge_index]
                if unknown:
                    raise RequestError(FAILED, "unknown_edge", f"steps[{i}].edges names {len(unknown)} edge(s) the "
                                       f"projection does not hold: {unknown[:10]}", {"edges": unknown[:100]})
                edges = {p._edge_index[e] for e in st["edges"]}
            plan.steps.append(SimpleNamespace(parent=index[st["from"]], child=index[st["var"]], fwd=fwd, rev=rev,
                                              tmask=tmask, nodes=nodes, edges=edges, optional=st["optional"],
                                              edge_var=st["edge_var"]))
    elif op == "support":
        plan.failed = _nodes(p, req["failed"], "failed")
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        plan.all_of = None if req["all_of"] is None else _types(p, req["all_of"], "all_of")
    else:
        plan.certificate = req["certificate"]
        plan.sources = _nodes(p, req["sources"], "sources")
        plan.targets = set(_nodes(p, req["targets"], "targets"))
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        plan.given, plan.cost, plan.zero, plan.rational = _costs(p, req["costs"], plan.fwd, plan.rev)
    return plan


def _node_filter(p: Projection, spec: Any, where: str) -> tuple[int | None, set[int] | None]:
    """(a type mask, an explicit node set): what a pattern variable may bind."""
    tmask = None if spec["node_types"] is None else _types(p, spec["node_types"], f"{where}.node_types")
    nodes = None if spec["nodes"] is None else set(_nodes(p, spec["nodes"], f"{where}.nodes"))
    return tmask, nodes


def _nodes(p: Projection, ids: Any, field: str) -> list[int]:
    missing = [n for n in ids if n not in p._node_index]
    if missing:
        raise RequestError(FAILED, "unknown_node", f"{field} names {len(missing)} node(s) the projection does not "
                           f"hold: {missing[:10]}", {"field": field, "nodes": missing[:100]})
    return [p._node_index[n] for n in ids]          # the request lists IDs in ascending order: so are the indices


def _scope(p: Projection, scope: Any, field: str) -> tuple[list[bool], list[bool]]:
    unknown = sorted({e["relation"] for e in scope if e["relation"] not in p._rel_index})
    if unknown:
        raise RequestError(FAILED, "unknown_relation", f"{field} names relations the projection does not declare: "
                           f"{unknown}", {"field": field, "relations": unknown})
    fwd, rev = [False] * len(p._relations), [False] * len(p._relations)
    for e in scope:
        marks = fwd if e["direction"] == FORWARD else rev
        for r in p._rel_cover[p._rel_index[e["relation"]]]:      # the relation and every relation below it
            marks[r] = True
    return fwd, rev


def _types(p: Projection, types: Any, field: str) -> int:
    unknown = sorted(t for t in types if t not in p._type_index)
    if unknown:
        raise RequestError(FAILED, "unknown_node_type", f"{field} names node types the projection does not declare: "
                           f"{unknown}", {"field": field, "node_types": unknown})
    mask = 0
    for t in types:
        mask |= 1 << p._type_index[t]
    return mask


def _costs(p: Projection, costs: Any, fwd: list[bool], rev: list[bool]) -> tuple[list, list, Any, bool]:
    """(the cost the request gives each edge in scope, its exact value, zero, rational): integers are added as
    integers; when the request gives any float, every cost is added as an exact fraction instead, so that no sum is
    rounded and every comparison is exact."""
    (kind, table), = costs.items()
    rel = p._edge_rel
    in_scope = [ei for ei in range(len(p._edge_ids)) if fwd[rel[ei]] or rev[rel[ei]]]
    given: list = [None] * len(p._edge_ids)
    if kind == "by_relation":
        unknown = sorted(r for r in table if r not in p._rel_index)
        if unknown:
            raise RequestError(FAILED, "unknown_relation", f"costs.by_relation names relations the projection does not "
                               f"declare: {unknown}", {"field": "costs", "relations": unknown})
        scoped = sorted({p._relations[rel[ei]] for ei in in_scope})
        missing = [r for r in scoped if r not in table]
        if missing:
            raise RequestError(FAILED, "missing_cost", f"costs.by_relation gives no cost for the relations in scope "
                               f"{missing}", {"relations": missing})
        for ei in in_scope:
            given[ei] = table[p._relations[rel[ei]]]
    else:
        unknown = sorted(e for e in table if e not in p._edge_index)
        if unknown:
            raise RequestError(FAILED, "unknown_edge", f"costs.by_edge names {len(unknown)} edge(s) the projection "
                               f"does not hold: {unknown[:10]}", {"edges": unknown[:100]})
        missing = [p._edge_ids[ei] for ei in in_scope if p._edge_ids[ei] not in table]
        if missing:
            raise RequestError(FAILED, "missing_cost", f"costs.by_edge gives no cost for {len(missing)} edge(s) in "
                               f"scope: {missing[:10]}", {"edges": missing[:100], "count": len(missing)})
        for ei in in_scope:
            given[ei] = table[p._edge_ids[ei]]
    rational = any(isinstance(x, float) for x in table.values())
    if not rational:
        return given, given, 0, False
    return given, [None if x is None else Fraction(x) for x in given], Fraction(0), True


def _hops(p: Projection, u: int, fwd: list[bool], rev: list[bool]) -> Iterator[tuple[int, int, int]]:
    """(edge, the node it leads to, 0 forward or 1 reverse) for every hop in scope from ``u``, in canonical order."""
    rel, src, dst = p._edge_rel, p._edge_src, p._edge_dst
    for ei in p._out[u]:
        if fwd[rel[ei]]:
            yield ei, dst[ei], 0
    for ei in p._in[u]:
        if rev[rel[ei]]:
            yield ei, src[ei], 1


def _back_hops(p: Projection, v: int, fwd: list[bool], rev: list[bool]) -> Iterator[tuple[int, int, int]]:
    """(edge, the node whose hop it is, 0 forward or 1 reverse) for every hop in scope that leads to ``v``, in
    canonical order: the hops of :func:`_hops` read backwards."""
    rel, src, dst = p._edge_rel, p._edge_src, p._edge_dst
    for ei in p._in[v]:
        if fwd[rel[ei]]:
            yield ei, src[ei], 0
    for ei in p._out[v]:
        if rev[rel[ei]]:
            yield ei, dst[ei], 1


def _via(p: Projection, u: int, ei: int, flag: int) -> dict:
    return {"edge": p._edge_ids[ei], "relation": p._relations[p._edge_rel[ei]], "direction": _DIRECTION[flag],
            "from": p._node_ids[u]}


# ---------------------------------------------------------------------------------------------- reachability, impact
def _search_bfs(p: Projection, plan: SimpleNamespace, ctl: _Control, strict: bool) -> SimpleNamespace:
    """Breadth-first search from ``plan.sources`` within the scope and ``max_depth``. A node is listed with its
    least depth and the hop it was first reached by. Reachability (``strict`` false) lists the sources at depth 0;
    dependency impact (``strict``) lists a node only when a hop reaches it, so a changed node is listed only when
    another change (or a cycle) reaches it, and it is expanded once, at depth 0."""
    st = SimpleNamespace(order=[], depth={}, parent={}, examined=0, discovered=0, interruption=None)
    order, depth, parent = st.order, st.depth, st.parent
    cap, max_depth = plan.max_results, plan.max_depth
    level: dict[int, int] = {}                     # the depth each node is expanded at
    queue: deque[int] = deque()
    for s in plan.sources:
        level[s] = 0
        queue.append(s)
        if not strict:
            if len(order) >= cap:
                st.discovered = len(plan.sources)
                st.interruption = "output_limit"
                return st
            depth[s] = 0
            order.append(s)
    examined = 0
    while queue and st.interruption is None:
        u = queue.popleft()
        d = level[u]
        if max_depth is not None and d >= max_depth:
            continue
        why = ctl.stop()
        if why:
            st.interruption = why
            break
        for ei, v, flag in _hops(p, u, plan.fwd, plan.rev):
            examined += 1
            if v in depth:
                continue
            if len(order) >= cap:
                st.interruption = "output_limit"
                break
            depth[v] = d + 1
            parent[v] = (u, ei, flag)
            order.append(v)
            if v not in level:
                level[v] = d + 1
                queue.append(v)
    st.examined, st.discovered = examined, len(level)
    return st


def _answer_bfs(p: Projection, plan: SimpleNamespace, st: SimpleNamespace, strict: bool) -> tuple[dict, list[str]]:
    items = []
    for v in sorted(st.order):
        par = st.parent.get(v)
        items.append({"node": p._node_ids[v], "depth": st.depth[v], "via": None if par is None else _via(p, *par)})
    if strict:
        answer = {"changed": [p._node_ids[s] for s in plan.sources], "affected": items, "count": len(items)}
    else:
        answer = {"reached": items, "count": len(items)}
    return answer, ([WITNESS_NOTE] if st.parent else [])


def _search_reachability(p, plan, ctl):
    return _search_bfs(p, plan, ctl, strict=False)


def _answer_reachability(p, plan, st):
    return _answer_bfs(p, plan, st, strict=False)


def _search_impact(p, plan, ctl):
    return _search_bfs(p, plan, ctl, strict=True)


def _answer_impact(p, plan, st):
    return _answer_bfs(p, plan, st, strict=True)


# ---------------------------------------------------------------------------------------------- typed path
def _search_typed(p: Projection, plan: SimpleNamespace, ctl: _Control) -> SimpleNamespace:
    """Breadth-first search over states (node, position in the pattern): a hop from position i follows the scope of
    pattern[i] and lands on a node of its node_types. A state is expanded once, so the search ends; a witness may
    visit a node more than once, at different positions."""
    positions, stride = plan.positions, len(plan.positions) + 1
    last = len(plan.positions)
    mask_of, targets = p._node_mask, plan.targets
    seen: dict[int, tuple[int, int, int] | None] = {}
    queue: deque[int] = deque()
    st = SimpleNamespace(goal=None, seen=seen, stride=stride, examined=0, discovered=0, interruption=None)
    for s in plan.sources:
        seen[s * stride] = None
        queue.append(s * stride)
    examined = 0
    while queue and st.goal is None:
        k = queue.popleft()
        u, i = divmod(k, stride)
        why = ctl.stop()
        if why:
            st.interruption = why
            break
        fwd, rev, tmask = positions[i]
        for ei, v, flag in _hops(p, u, fwd, rev):
            examined += 1
            if tmask is not None and not mask_of[v] & tmask:
                continue
            nk = v * stride + i + 1
            if nk in seen:
                continue
            seen[nk] = (k, ei, flag)
            if i + 1 < last:
                queue.append(nk)
            elif targets is None or v in targets:
                st.goal = nk
                break
    st.examined, st.discovered = examined, len(seen)
    return st


def _answer_typed(p: Projection, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    if st.goal is None:
        certificate = None
        if plan.certificate and not st.interruption:     # the closed set of states proves there is no witness
            last, ids = len(plan.positions), p._node_ids
            states = sorted((k % st.stride, k // st.stride) for k in st.seen if k % st.stride < last)
            certificate = {"kind": "closed_states",
                           "states": [{"node": ids[u], "position": i} for i, u in states]}
        return {"found": None if st.interruption else False, "witness": None, "certificate": certificate}, \
            ([CERTIFIED_NOTE] if certificate else [])
    hops, k = [], st.goal
    while st.seen[k] is not None:
        pk, ei, flag = st.seen[k]
        hop = _via(p, pk // st.stride, ei, flag)
        hops.append({"position": pk % st.stride, **hop, "to": p._node_ids[k // st.stride]})
        k = pk
    hops.reverse()
    witness = {"source": p._node_ids[k // st.stride], "target": p._node_ids[st.goal // st.stride],
               "length": len(hops), "hops": hops}
    return {"found": True, "witness": witness, "certificate": None}, [WITNESS_NOTE]


# ---------------------------------------------------------------------------------------------- shortest path
def _search_shortest(p: Projection, plan: SimpleNamespace, ctl: _Control) -> SimpleNamespace:
    """Dijkstra's algorithm from every source at once, with exact arithmetic (:func:`_costs`). The first target
    settled is the answer: no other target costs less, because every cost is non-negative."""
    dist: dict[int, Any] = {}
    parent: dict[int, tuple[int, int, int]] = {}
    settled: set[int] = set()
    st = SimpleNamespace(goal=None, dist=dist, parent=parent, settled=settled, lower=None, examined=0, discovered=0,
                         interruption=None)
    at_zero = [s for s in plan.sources if s in plan.targets]
    if at_zero:                                   # a source that is a target costs nothing to reach
        st.goal = at_zero[0]
        dist[st.goal] = plan.zero
        st.discovered = len(plan.sources)
        return st
    heap = [(plan.zero, s) for s in plan.sources]
    for s in plan.sources:
        dist[s] = plan.zero
    heapq.heapify(heap)
    cost = plan.cost
    examined = 0
    while heap:
        c, u = heapq.heappop(heap)
        if u in settled or c > dist[u]:
            continue                              # an entry superseded by a cheaper one
        if u in plan.targets:
            st.goal = u
            break
        why = ctl.stop()
        if why:
            st.interruption, st.lower = why, c    # every node not yet settled costs at least c
            break
        settled.add(u)
        for ei, v, flag in _hops(p, u, plan.fwd, plan.rev):
            examined += 1
            if v in settled:
                continue
            nc = c + cost[ei]
            old = dist.get(v)
            if old is None or nc < old:
                dist[v] = nc
                parent[v] = (u, ei, flag)
                heapq.heappush(heap, (nc, v))
    st.examined, st.discovered = examined, len(dist)
    return st


def _number(x: Any, rational: bool) -> tuple[Any, str | None]:
    """A cost as JSON gives it: an integer stays exact; in rational mode, the nearest float (null when the value is
    beyond the floats) and the exact value as ``"p/q"`` (or ``"p"``)."""
    if not rational:
        return x, None
    x = Fraction(x)
    try:
        near = float(x)
    except OverflowError:
        near = None
    return near, (str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}")


def _answer_shortest(p: Projection, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    def costed(field: str, x: Any) -> dict:
        value, exact = _number(x, plan.rational)
        return {field: value, **({f"{field}_exact": exact} if plan.rational else {})}

    def witness(t: int) -> dict:
        hops, v = [], t
        while v in st.parent:
            u, ei, flag = st.parent[v]
            hops.append({**_via(p, u, ei, flag), "to": p._node_ids[v], "cost": plan.given[ei]})
            v = u
        hops.reverse()
        return {"source": p._node_ids[v], "target": p._node_ids[t], "length": len(hops), "hops": hops}

    def exact(x: Any) -> Any:
        return _number(x, True)[1] if plan.rational else x

    def potentials(bound: Any) -> dict | None:
        """Settled distances below ``bound``: every other node counts ``bound``. No hop in scope lowers them (a node
        settled at cost d has no hop to a node above d plus its cost), every source counts 0 and every target counts
        at least ``bound``, so no path from a source to a target costs less than ``bound``."""
        if not plan.certificate:
            return None
        below = sorted(v for v in st.settled if st.dist[v] < bound)
        return {"kind": "potentials", "bound": exact(bound),
                "potentials": [{"node": p._node_ids[v], "value": exact(st.dist[v])} for v in below]}

    if st.goal is not None:
        cert = potentials(st.dist[st.goal])
        return ({"found": True, "optimal": True, **costed("cost", st.dist[st.goal]), "witness": witness(st.goal),
                 "certificate": cert}, [WITNESS_NOTE] + ([CERTIFIED_NOTE] if cert else []))
    if st.interruption is None:
        cert = ({"kind": "closure", "reached": [p._node_ids[v] for v in sorted(st.dist)]}
                if plan.certificate else None)                # everything reachable, and no target among it
        return ({"found": False, "optimal": None, "cost": None, "witness": None, "certificate": cert},
                [CERTIFIED_NOTE] if cert else [])
    lower = costed("lower_bound", st.lower) if st.lower is not None else {"lower_bound": None}
    cert = potentials(st.lower) if st.lower is not None else None
    notes = [CERTIFIED_NOTE] if cert else []
    known = sorted((st.dist[t], t) for t in plan.targets if t in st.dist)
    if not known:
        return {"found": None, "optimal": None, "cost": None, **lower, "witness": None, "certificate": cert}, notes
    c, t = known[0]
    return ({"found": True, "optimal": False, **costed("cost", c), **lower, "witness": witness(t),
             "certificate": cert}, [TENTATIVE_NOTE, WITNESS_NOTE] + notes)


# ---------------------------------------------------------------------------------------------- pattern
def _search_pattern(p: Projection, plan: SimpleNamespace, ctl: _Control) -> SimpleNamespace:
    """Match a tree pattern without dead ends (Yannakakis' semi-join reduction, over the region the match reaches).

    1. Downward, the only pass that expands states: from each variable's candidates, scan each step's hops once, and
       keep the hops to nodes the step's constraints admit; the children reached are the child's candidates.
    2. Upward: a required step keeps only the parents with a hop into its child's remaining candidates.
    3. Downward: each step keeps the hops from its parent's remaining candidates into its child's, and the child keeps
       the nodes those hops reach.

    Every remaining value then completes to a match, so the rows are enumerated, and counted, from the kept hops
    alone, in time proportional to the input scanned and the rows returned."""
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, kept=None, hops=None, scanned=None)
    tmask0, nodes0 = plan.match
    if nodes0 is not None:
        root = {u for u in nodes0 if tmask0 is None or p._node_mask[u] & tmask0}
    else:
        root = {u for u in range(len(p._node_ids)) if p._node_mask[u] & tmask0}
    cand: list[set[int]] = [set() for _ in plan.vars]
    cand[0] = root
    found: list[dict[int, list]] = []
    mask_of = p._node_mask
    examined = 0
    for s in plan.steps:                                     # 1: discover, downward
        hops: dict[int, list] = {}
        child = cand[s.child]
        for u in sorted(cand[s.parent]):
            why = ctl.stop()
            if why:
                st.interruption, st.examined = why, examined
                st.discovered = sum(len(c) for c in cand)
                return st
            kept, seen = [], set()
            for ei, w, flag in _hops(p, u, s.fwd, s.rev):
                examined += 1
                # a self-loop in a scope of both directions joins u to itself twice by one edge: one row, not two
                if (s.edges is None or ei in s.edges) and (s.tmask is None or mask_of[w] & s.tmask) \
                        and (s.nodes is None or w in s.nodes) and (ei, w) not in seen:
                    seen.add((ei, w))
                    kept.append((ei, w, flag))
                    child.add(w)
            hops[u] = kept
        found.append(hops)
    st.scanned = [len(c) for c in cand]
    for i in range(len(plan.steps) - 1, -1, -1):              # 2: prune parents, upward
        s = plan.steps[i]
        if not s.optional:
            child = cand[s.child]
            cand[s.parent] = {u for u in cand[s.parent] if any(w in child for _, w, _ in found[i].get(u, ()))}
    kept_hops: list[dict[int, list]] = []
    for i, s in enumerate(plan.steps):                       # 3: prune children, downward
        child, reached, hops = cand[s.child], set(), {}
        for u in cand[s.parent]:
            hs = [h for h in found[i].get(u, ()) if h[1] in child]
            hops[u] = hs
            reached.update(w for _, w, _ in hs)
        cand[s.child] = reached
        kept_hops.append(hops)
    st.kept, st.hops, st.examined = cand, kept_hops, examined
    st.discovered = sum(st.scanned)
    return st


def _pattern_total(plan: SimpleNamespace, st: SimpleNamespace) -> int:
    """The number of rows, exactly, without enumerating them: per variable value, the product over its steps of the
    number of ways to continue (an optional step with no hop continues one way, unbound)."""
    ways = [dict.fromkeys(c, 1) for c in st.kept]
    for i in range(len(plan.steps) - 1, -1, -1):
        s = plan.steps[i]
        child_ways = ways[s.child]
        parent_ways = ways[s.parent]
        for u in parent_ways:
            hs = st.hops[i].get(u, ())
            n = sum(child_ways[w] for _, w, _ in hs)
            parent_ways[u] *= n if hs else (1 if s.optional else 0)
    return sum(ways[0].values())


def _answer_pattern(p: Projection, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    names = plan.vars
    if st.interruption is not None and st.kept is None:       # stopped while discovering: nothing is known
        return {"exists": None, "total": None, "rows": [], "count": 0, "coverage": None}, []
    total = _pattern_total(plan, st)
    coverage = {"vars": [{"var": names[i], "candidates": st.scanned[i], "kept": len(st.kept[i])}
                         for i in range(len(names))],
                "steps": [sum(len(h) for h in st.hops[i].values()) for i in range(len(plan.steps))]}
    rows: list[dict] = []
    if plan.answer == "rows":
        cap = plan.max_results
        ids, eids = p._node_ids, p._edge_ids
        val: list = [None] * len(names)
        link: list = [None] * len(plan.steps)
        steps = plan.steps

        def emit() -> None:
            rows.append({"nodes": {names[i]: (None if v is None else ids[v]) for i, v in enumerate(val)},
                         "edges": {s.edge_var: (None if link[j] is None else eids[link[j]])
                                   for j, s in enumerate(steps) if s.edge_var is not None},
                         "links": [None if x is None else eids[x] for x in link]})

        def extend(i: int) -> bool:
            """Bind steps i and on; False once the rows reach the cap."""
            if i == len(steps):
                if len(rows) >= cap:
                    return False
                emit()
                return True
            s = steps[i]
            u = val[s.parent]
            hs = () if u is None else st.hops[i].get(u, ())
            if not hs:                                       # an unbound or unmatched optional step
                val[s.child], link[i] = None, None
                return extend(i + 1)
            for ei, w, _ in hs:
                val[s.child], link[i] = w, ei
                if not extend(i + 1):
                    return False
            return True
        for u in sorted(st.kept[0]):
            val[0] = u
            if not extend(0):
                st.interruption = "output_limit"
                break
    answer = {"exists": total > 0, "total": total, "rows": rows, "count": len(rows), "coverage": coverage}
    return answer, ([WITNESS_NOTE] if rows else [])


# ---------------------------------------------------------------------------------------------- support
def _search_support(p: Projection, plan: SimpleNamespace, ctl: _Control) -> SimpleNamespace:
    """What stops standing when the failed nodes fail, and what stands on less than before.

    A hop in scope leads from a node to one of its supports. A node with no support stands (it is a ground: a source,
    an axiom); a node of an ``all_of`` type stands when every support stands; any other node when one does. What
    stands is the least fixpoint of these rules, so support in a circle grounds nothing. A failed node never stands.

    1. The region: the failed nodes and everything that depends on them (their hops read backwards).
    2. The closure: everything the region's standing depends on (its hops read forwards); the rest of the
       projection cannot change the answer.
    3. Two least fixpoints over the closure, by counting each node's standing supports: before (nothing failed) and
       after (the failed nodes held down).
    4. The causes: the failed nodes each fallen node, and each node that lost a support, rests on, carried as bits
       along the fallen supports."""
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, done=False)
    fwd, rev = plan.fwd, plan.rev
    failed = set(plan.failed)
    examined = 0

    def stopped() -> bool:
        why = ctl.stop()
        if why:
            st.interruption, st.examined = why, examined
        return why is not None

    region, queue = set(failed), deque(plan.failed)          # 1: the failed nodes and their dependents
    while queue:
        v = queue.popleft()
        if stopped():
            st.discovered = len(region)
            return st
        for _, u, _ in _back_hops(p, v, fwd, rev):
            examined += 1
            if u not in region:
                region.add(u)
                queue.append(u)
    supports: dict[int, list[tuple[int, int, int]]] = {}     # 2: their support closure, each node's supports once
    closure, queue = set(region), deque(sorted(region))
    while queue:
        u = queue.popleft()
        if stopped():
            st.discovered = len(closure)
            return st
        sup, seen = [], set()
        for ei, v, flag in _hops(p, u, fwd, rev):
            examined += 1
            if v not in seen:                                 # parallel hops to one support: one support
                seen.add(v)
                sup.append((v, ei, flag))
                if v not in closure:
                    closure.add(v)
                    queue.append(v)
        supports[u] = sup
    st.discovered = len(closure)
    dependents: dict[int, list[int]] = {u: [] for u in closure}
    for u in sorted(closure):
        for v, _, _ in supports[u]:
            dependents[v].append(u)
    every, mask_of = plan.all_of, p._node_mask
    need0 = {u: (len(supports[u]) if every is not None and mask_of[u] & every else min(1, len(supports[u])))
             for u in closure}

    def standing(down: set[int]) -> set[int] | None:          # 3: a least fixpoint, by counting standing supports
        nonlocal examined
        need, stands = dict(need0), set()
        queue = deque(u for u in sorted(closure) if need[u] == 0 and u not in down)
        stands.update(queue)
        while queue:
            v = queue.popleft()
            if stopped():
                return None
            for u in dependents[v]:
                examined += 1
                if u in stands or u in down:
                    continue
                need[u] -= 1
                if need[u] == 0:
                    stands.add(u)
                    queue.append(u)
        return stands

    before = standing(set())
    after = standing(failed) if before is not None else None
    if after is None:
        return st
    fallen = before - after                                  # stood before, not after (failed nodes included)
    lost = sorted(u for u in region if u in fallen and u not in failed)
    weakened = sorted(u for u in region if u in after and any(v in fallen for v, _, _ in supports[u]))
    roots = [r for r in plan.failed if r in fallen]           # 4: the causes, as bits of the failed nodes that fell
    cause = {r: 1 << k for k, r in enumerate(roots)}
    lost_set, weak_set = set(lost), set(weakened)
    queue = deque(roots)
    while queue:
        v = queue.popleft()
        if stopped():
            return st
        bits = cause[v]
        for u in dependents[v]:
            examined += 1
            if u in lost_set or u in weak_set:
                old = cause.get(u, 0)
                if old | bits != old:
                    cause[u] = old | bits
                    if u in lost_set:                         # a node that still stands passes nothing on
                        queue.append(u)
    st.examined, st.done = examined, True
    st.supports, st.after, st.fallen, st.roots, st.cause = supports, after, fallen, roots, cause
    st.lost, st.weakened = lost, weakened
    st.ungrounded = sum(1 for u in region if u not in before and u not in failed)
    return st


def _answer_support(p: Projection, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    failed = [p._node_ids[r] for r in plan.failed]
    if not st.done:                                          # stopped before both fixpoints: nothing is known
        return {"failed": failed, "lost": None, "weakened": None, "counts": None}, []
    ids = p._node_ids

    def entry(u: int) -> dict:
        sup, bits = st.supports[u], st.cause.get(u, 0)
        return {"node": ids[u], "supports": len(sup), "standing": sum(1 for v, _, _ in sup if v in st.after),
                "failing": [{**_via(p, u, ei, flag), "to": ids[v]} for v, ei, flag in sup if v in st.fallen],
                "causes": [ids[r] for k, r in enumerate(st.roots) if bits >> k & 1]}

    cap = plan.max_results
    lost, weakened = st.lost[:cap], st.weakened[:max(0, cap - len(st.lost))]
    if len(lost) + len(weakened) < len(st.lost) + len(st.weakened):
        st.interruption = "output_limit"
    answer = {"failed": failed, "lost": [entry(u) for u in lost], "weakened": [entry(u) for u in weakened],
              "counts": {"lost": len(st.lost), "weakened": len(st.weakened), "ungrounded": st.ungrounded}}
    return answer, ([WITNESS_NOTE] if lost or weakened else [])


_OPERATIONS = {"reachability": (_search_reachability, _answer_reachability),
               "dependency_impact": (_search_impact, _answer_impact),
               "typed_path": (_search_typed, _answer_typed),
               "shortest_path": (_search_shortest, _answer_shortest),
               "pattern": (_search_pattern, _answer_pattern),
               "support": (_search_support, _answer_support)}
