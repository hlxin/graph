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

from . import algebra, causal, dominators, flows, paths, ranking, schedule, structure
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
    from .periodic import PeriodicProjection
    if not isinstance(projection, (Projection, PeriodicProjection)):
        raise TypeError(f"execute runs on a Projection or a PeriodicProjection, not {type(projection).__name__}")
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
    periodic = not isinstance(projection, Projection)
    try:
        if periodic:
            from . import periodic as lazy
            plan, operations = lazy._bind(projection, req), lazy._OPERATIONS
        else:
            plan, operations = _bind(projection, req), _OPERATIONS
    except RequestError as e:
        return _no_answer(e.status, e.code, e.message, e.detail, request=req, projection=projection.identity,
                          started=started)

    bound = time.perf_counter()
    ctl = _Control(req.limits["max_expansions"], deadline, clock, cancelled, check_interval)
    search, build = operations[req.operation]
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
        names = [m["var"]] + [st["var"] for st in req["steps"] if "var" in st]
        index = {name: i for i, name in enumerate(names)}
        plan.vars, plan.answer = names, req["answer"]
        plan.match = _node_filter(p, m, "match")
        plan.steps = []
        plan.cyclic = any("to" in st for st in req["steps"])
        for i, st in enumerate(req["steps"]):
            fwd, rev = _scope(p, st["scope"], f"steps[{i}].scope")
            closing = "to" in st
            tmask, nodes = (None, None) if closing else _node_filter(p, st, f"steps[{i}]")
            edges = None
            if st["edges"] is not None:
                unknown = [e for e in st["edges"] if e not in p._edge_index]
                if unknown:
                    raise RequestError(FAILED, "unknown_edge", f"steps[{i}].edges names {len(unknown)} edge(s) the "
                                       f"projection does not hold: {unknown[:10]}", {"edges": unknown[:100]})
                edges = {p._edge_index[e] for e in st["edges"]}
            plan.steps.append(SimpleNamespace(parent=index[st["from"]], child=index[st["to" if closing else "var"]],
                                              fwd=fwd, rev=rev, tmask=tmask, nodes=nodes, edges=edges,
                                              optional=False if closing else st["optional"],
                                              edge_var=st["edge_var"], closing=closing))
    elif op == "bottleneck_path":
        plan.certificate, plan.objective = req["certificate"], req["objective"]
        plan.sources = _nodes(p, req["sources"], "sources")
        plan.targets = set(_nodes(p, req["targets"], "targets"))
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        plan.weight = _costs(p, req["weights"], plan.fwd, plan.rev, "weights", scaled=False)
    elif op == "regular_path":
        plan.certificate, plan.answer = req["certificate"], req["answer"]
        plan.sources = _nodes(p, req["sources"], "sources")
        plan.targets = None if req["targets"] is None else set(_nodes(p, req["targets"], "targets"))
        plan.auto = paths.automaton(req["path"])
        plan.states, plan.step_of = _path_states(p, plan.auto)
    elif op == "derivation":
        plan.certificate, plan.objective = req["certificate"], req["objective"]
        plan.targets = _nodes(p, req["targets"], "targets")
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        plan.all_of = None if req["all_of"] is None else _types(p, req["all_of"], "all_of")
        plan.grounds = None if req["grounds"] is None else set(_nodes(p, req["grounds"], "grounds"))
        plan.cost = (None if req["costs"] is None
                     else _costs(p, req["costs"], plan.fwd, plan.rev, scaled=req["objective"] == "min_sum"))
        plan.rational = plan.cost is not None and plan.cost.rational
    elif op == "personalized_pagerank":
        plan.certificate, plan.top = req["certificate"], req["top"]
        plan.sources = _nodes(p, req["sources"], "sources")
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        plan.alpha, plan.tolerance = Fraction(req["alpha"]), Fraction(req["tolerance"])
    elif op in ("components", "biconnectivity"):
        plan.certificate = req["certificate"]
        plan.nodes = None if req["nodes"] is None else _nodes(p, req["nodes"], "nodes")
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        if op == "components":
            plan.connectivity, plan.min_size = req["connectivity"], req["min_size"]
        else:
            plan.blocks = req["blocks"]
    elif op == "max_flow":
        plan.certificate = req["certificate"]
        plan.sources, plan.targets = _nodes(p, req["sources"], "sources"), _nodes(p, req["targets"], "targets")
        plan.source_set, plan.target_set = set(plan.sources), set(plan.targets)
        plan.terminal_set = plan.source_set | plan.target_set
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        _flow_capacities(p, plan, req["capacities"], req["node_capacity"])
    elif op == "matching":
        plan.certificate = req["certificate"]
        tmask, nodes = _node_filter(p, req["left"], "left")
        masks = p._node_mask
        plan.left = {u for u in (nodes if nodes is not None else range(len(p._node_ids)))
                     if tmask is None or masks[u] & tmask}
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
    elif op == "dominators":
        plan.certificate = req["certificate"]
        plan.sources = _nodes(p, req["sources"], "sources")
        plan.targets = None if req["targets"] is None else _nodes(p, req["targets"], "targets")
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
    elif op == "d_separation":
        plan.certificate = req["certificate"]
        plan.x, plan.y, plan.given = _nodes(p, req["x"], "x"), _nodes(p, req["y"], "y"), _nodes(p, req["given"], "given")
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
    elif op == "critical_path":
        plan.certificate, plan.schedule, plan.costs = req["certificate"], req["answer"] == "schedule", req["costs"]
        plan.sources = None if req["sources"] is None else _nodes(p, req["sources"], "sources")
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        if plan.costs is None:
            plan.rational, plan.cost_of, plan.given, plan.exact = False, (lambda ei: 1), (lambda ei: 1), (lambda x: x)
        else:
            table = _costs(p, req["costs"], plan.fwd, plan.rev)
            edge, rel_cost, edge_rel = table.edge, table.rel, p._edge_rel
            plan.rational, plan.given, plan.exact = table.rational, table.given, table.exact
            plan.cost_of = ((lambda ei: edge[ei]) if edge is not None else (lambda ei: rel_cost[edge_rel[ei]]))
    elif op == "incidence":
        plan.certificate, plan.left, plan.right = req["certificate"], "left" in req["kernels"], "right" in req["kernels"]
        masks = p._node_mask
        chosen = []
        for field in ("rows", "columns"):
            tmask, nodes = _node_filter(p, req[field], field)
            chosen.append(sorted(u for u in (nodes if nodes is not None else range(len(p._node_ids)))
                                 if tmask is None or masks[u] & tmask))
        plan.rows, plan.columns = chosen
        both = sorted(set(plan.rows) & set(plan.columns))
        if both:
            raise RequestError(FAILED, "invalid_request", f"rows and columns are disjoint; both hold "
                               f"{[p._node_ids[u] for u in both[:10]]}", {"nodes": [p._node_ids[u] for u in both[:100]]})
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        table = _costs(p, req["coefficients"], plan.fwd, plan.rev, "coefficients", scaled=False)
        plan.coefficient = lambda ei: Fraction(table.given(ei))
    elif op == "periodicity":
        raise RequestError("unsupported", "unsupported_operation", "periodicity runs on a periodic projection: the "
                           "translation lattice of a component of an infinite periodic graph",
                           {"operation": op, "projection": "plain"})
    elif op == "support":
        plan.failed = _nodes(p, req["failed"], "failed")
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        plan.all_of = None if req["all_of"] is None else _types(p, req["all_of"], "all_of")
    else:
        plan.certificate = req["certificate"]
        plan.sources = _nodes(p, req["sources"], "sources")
        plan.targets = set(_nodes(p, req["targets"], "targets"))
        plan.fwd, plan.rev = _scope(p, req["scope"], "scope")
        plan.cost = _costs(p, req["costs"], plan.fwd, plan.rev)
        plan.rational = plan.cost.rational
    return plan


def _flow_capacities(p: Projection, plan: SimpleNamespace, capacities: Any, node_capacity: Any) -> None:
    """A max-flow request's capacities as integers over one scale: ``plan.capacity(ei)``, ``plan.node_cap`` (None:
    no node limit), ``plan.scale`` and ``plan.rational``. Integer capacities are used as they are; when any capacity
    is a float, every capacity is an exact rational over the largest power-of-two denominator among them, and is
    held as an integer count of 1/scale (as shortest-path costs are)."""
    table = None if capacities is None else _costs(p, capacities, plan.fwd, plan.rev, "capacities")
    scale = table.scale if table is not None and table.rational else 1
    if isinstance(node_capacity, float):
        scale = max(scale, node_capacity.as_integer_ratio()[1])
    plan.scale, plan.rational = scale, scale > 1 or isinstance(node_capacity, float) or (
        table is not None and table.rational)
    plan.node_cap = None if node_capacity is None else int(Fraction(node_capacity) * scale)
    if table is None:
        plan.capacity = lambda ei: scale
    else:
        factor = scale // table.scale
        edge, rel, edge_rel = table.edge, table.rel, p._edge_rel
        plan.capacity = ((lambda ei: edge[ei] * factor) if edge is not None
                         else (lambda ei: rel[edge_rel[ei]] * factor))


def _path_states(p: Projection, auto: paths.Automaton) -> tuple[list, list]:
    """For each state q of a path automaton that has moves, what one hop from it can do: ``(sides, by_relation)``,
    where ``sides`` are the hops of every step it can move to (:func:`_sides`), and ``by_relation[flag][r]`` lists
    ``(state, type mask)`` for the steps a hop of relation r in direction flag (0 forward, 1 reverse) matches, in
    ascending state order. A state with no moves has None. Also each state's step as ``(fwd, rev, type mask)`` (None
    for the start). Each distinct step is checked against the projection once (unknown relations and types fail as
    for every scope)."""
    compiled: dict[int, tuple] = {}
    step_of: list = [None]
    for i, step in enumerate(auto.steps[1:], 1):
        key = id(step)                    # a step written out several times by a bounded repetition: one object
        if key not in compiled:
            fwd, rev = _scope(p, step["scope"], f"path step {i} scope")
            mask = None if step["node_types"] is None else _types(p, step["node_types"], f"path step {i} node_types")
            compiled[key] = (fwd, rev, mask)
        step_of.append(compiled[key])
    relations = range(len(p._relations))
    out: list = []
    for q, moves in enumerate(auto.follow):
        if not moves:
            out.append(None)
            continue
        by_relation = tuple(tuple(tuple((i, step_of[i][2]) for i in moves if step_of[i][flag][r]) for r in relations)
                            for flag in (0, 1))
        fwd = [bool(x) for x in by_relation[0]]
        rev = [bool(x) for x in by_relation[1]]
        out.append((_sides(p, fwd, rev), by_relation))
    return out, step_of


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


class _Costs:
    """The costs (or weights) a request gives the edges in scope, compiled for a search.

    ``edge`` (a table ``by_edge``: by edge index) or ``rel`` (``by_relation``: by relation index) holds the value a
    search computes with, read as ``edge[ei]`` or ``rel[edge_rel[ei]]``. Integers are used as they are. When the
    request gives any float (``rational``), every cost is an exact rational with a power-of-two denominator, so each
    is held as an integer count of ``1/scale``, where ``scale`` is the largest denominator: sums and comparisons of
    these integers are those of the exact values, without the cost of fractions, and :meth:`exact` turns one back.
    Weights (``scaled`` false) are only compared, so they are kept as given. ``costs[ei]`` is the exact value of an
    edge's cost, and :meth:`given` the number the request wrote for it."""

    __slots__ = ("edge", "rel", "edge_rel", "given_edge", "given_rel", "rational", "scale")

    def __init__(self, p: Projection, table: Mapping, given_edge: list | None, scaled: bool):
        """``given_edge``: a ``by_edge`` table's values by edge index (None for a ``by_relation`` table)."""
        self.edge_rel, self.rational, self.scale = p._edge_rel, False, 1
        self.given_edge = given_edge
        self.given_rel = None if given_edge is not None else [table.get(r) for r in p._relations]
        values = given_edge if given_edge is not None else self.given_rel
        if scaled and any(type(x) is float for x in table.values()):
            ratio = {x: x.as_integer_ratio() for x in set(table.values())}
            self.scale = scale = max(d for _, d in ratio.values())
            exact = {x: n * (scale // d) for x, (n, d) in ratio.items()}
            values = [None if x is None else exact[x] for x in values]
            self.rational = True
        self.edge = values if given_edge is not None else None
        self.rel = values if given_edge is None else None

    def given(self, ei: int) -> Any:
        """The cost (or weight) the request wrote for edge ``ei``."""
        return self.given_edge[ei] if self.given_edge is not None else self.given_rel[self.edge_rel[ei]]

    def exact(self, x: Any) -> Any:
        """A sum of search values as its exact number: an integer, or in rational mode a fraction."""
        return Fraction(x, self.scale) if self.rational else x

    def __getitem__(self, ei: int) -> Any:
        """The exact cost of edge ``ei`` (what revalidation compares certificate values with)."""
        x = self.edge[ei] if self.edge is not None else self.rel[self.edge_rel[ei]]
        return Fraction(x, self.scale) if self.rational else x


def _costs(p: Projection, costs: Any, fwd: list[bool], rev: list[bool], field: str = "costs",
           scaled: bool = True) -> _Costs:
    """The request's cost table checked against the projection and compiled (:class:`_Costs`): every relation or edge
    it names exists, and every edge in scope has a cost. Integers are added as integers; when the request gives any
    float, every cost is added exactly as a rational instead, so that no sum is rounded and every comparison is exact.
    ``field`` names the table in messages; bottleneck weights (``scaled`` false) use it too.

    The checks read the per-relation edge counts (:func:`_relation_counts`) and the table, not every edge in scope: a
    ``by_relation`` table is compiled in time proportional to the relations, a ``by_edge`` one to its entries."""
    (kind, table), = costs.items()
    counts = _relation_counts(p)
    scoped = [r for r in range(len(counts)) if counts[r] and (fwd[r] or rev[r])]
    if kind == "by_relation":
        unknown = sorted(r for r in table if r not in p._rel_index)
        if unknown:
            raise RequestError(FAILED, "unknown_relation", f"{field}.by_relation names relations the projection does "
                               f"not declare: {unknown}", {"field": field, "relations": unknown})
        missing = [p._relations[r] for r in scoped if p._relations[r] not in table]
        if missing:
            raise RequestError(FAILED, "missing_cost", f"{field}.by_relation gives nothing for the relations in "
                               f"scope {missing}", {"field": field, "relations": missing})
        return _Costs(p, table, None, scaled)
    index, rel = p._edge_index, p._edge_rel
    given: list = [None] * len(p._edge_ids)
    covered, unknown = 0, []
    for e, x in table.items():                                 # one pass: unknown edges, values, edges in scope
        ei = index.get(e)
        if ei is None:
            unknown.append(e)
            continue
        given[ei] = x
        if fwd[rel[ei]] or rev[rel[ei]]:
            covered += 1
    if unknown:
        unknown.sort()
        raise RequestError(FAILED, "unknown_edge", f"{field}.by_edge names {len(unknown)} edge(s) the "
                           f"projection does not hold: {unknown[:10]}", {"field": field, "edges": unknown[:100]})
    if covered < sum(counts[r] for r in scoped):               # some edge in scope has no cost: name them
        missing = [p._edge_ids[ei] for ei in range(len(p._edge_ids))
                   if (fwd[rel[ei]] or rev[rel[ei]]) and given[ei] is None]
        raise RequestError(FAILED, "missing_cost", f"{field}.by_edge gives nothing for {len(missing)} edge(s) "
                           f"in scope: {missing[:10]}", {"field": field, "edges": missing[:100],
                                                         "count": len(missing)})
    return _Costs(p, table, given, scaled)


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


_MEMO_LISTS = 16                         # compiled edge lists a projection keeps (by end and relation selection)


def _relation_counts(p: Projection) -> tuple[int, ...]:
    """How many edges each relation has (by relation index), counted once per projection."""
    counts = p._memo.get("relation_counts")
    if counts is None:
        tally = [0] * len(p._relations)
        for r in p._edge_rel:
            tally[r] += 1
        counts = p._memo["relation_counts"] = tuple(tally)
    return counts


def _edge_lists(p: Projection, marks: list[bool], incoming: bool) -> Any:
    """Per node, the edges at one of its ends whose relation ``marks`` selects, in ascending edge ID: the edges it is
    the source of (``incoming`` false) or the target of. These are a node's hops in one direction, compiled once and
    kept on the projection, so that a search reads plain lists rather than filtering every edge it meets.

    A selection of every relation the edges use is the projection's own adjacency, and a selection of none is empty
    lists, both shared at no cost. Any other selection is a list whose entries stay None until a search first reads
    that node and fills it (``lists[u] = [ei for ei in base[u] if marks[rel[ei]]]``): a search that reaches a few
    nodes of a large projection compiles only those."""
    memo = p._memo
    key = ("in" if incoming else "out", tuple(marks))
    kept = memo.setdefault("edge_lists", {})
    lists = kept.get(key)
    if lists is None:
        counts = _relation_counts(p)
        chosen = [marks[r] for r in range(len(counts)) if counts[r]]
        if all(chosen):
            lists = p._in if incoming else p._out
        elif not any(chosen):
            lists = memo.get("no_edges")
            if lists is None:
                lists = memo["no_edges"] = ((),) * len(p._node_ids)
        else:
            lists = [None] * len(p._node_ids)
        while len(kept) >= _MEMO_LISTS:                       # the oldest selection goes first
            kept.pop(next(iter(kept)), None)
        kept[key] = lists
    return lists


def _sides(p: Projection, fwd: list[bool], rev: list[bool]) -> tuple[tuple, tuple]:
    """A scope's hops from a node, as two sides to read in order: ``(lists, base, marks, ends, flag)`` for its
    forward hops (the edges it is the source of, leading to their targets) and then its reverse hops. ``lists[u]`` is
    None until filled from ``base[u]`` by ``marks`` (:func:`_edge_lists`); ``ends[ei]`` is the node a hop leads to."""
    return ((_edge_lists(p, fwd, False), p._out, fwd, p._edge_dst, 0),
            (_edge_lists(p, rev, True), p._in, rev, p._edge_src, 1))


def _back_sides(p: Projection, fwd: list[bool], rev: list[bool]) -> tuple[tuple, tuple]:
    """The hops of :func:`_sides` read backwards: for a node v, the hops in scope that lead to it, as ``(lists, base,
    marks, ends, flag)`` where ``ends[ei]`` is the node whose hop it is (the order of :func:`_back_hops`)."""
    return ((_edge_lists(p, fwd, True), p._in, fwd, p._edge_src, 0),
            (_edge_lists(p, rev, False), p._out, rev, p._edge_dst, 1))


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
    sides, rel = _sides(p, plan.fwd, plan.rev), p._edge_rel
    examined, full = 0, False
    while queue and not full:
        u = queue.popleft()
        d = level[u]
        if max_depth is not None and d >= max_depth:
            continue
        why = ctl.stop()
        if why:
            st.interruption = why
            break
        d += 1
        for lists, base, marks, ends, flag in sides:
            hs = lists[u]
            if hs is None:
                hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
            examined += len(hs)
            for ei in hs:
                v = ends[ei]
                if v in depth:
                    continue
                if len(order) >= cap:
                    full = True
                    examined -= len(hs) - 1 - hs.index(ei)        # the hops after this one were not examined
                    break
                depth[v] = d
                parent[v] = (u, ei, flag)
                order.append(v)
                if v not in level:
                    level[v] = d
                    queue.append(v)
            if full:
                st.interruption = "output_limit"
                break
    st.examined, st.discovered = examined, len(level)
    return st


def _answer_bfs(p: Projection, plan: SimpleNamespace, st: SimpleNamespace, strict: bool) -> tuple[dict, list[str]]:
    ids, eids, names, rel, depth, parent = p._node_ids, p._edge_ids, p._relations, p._edge_rel, st.depth, st.parent
    items = []
    for v in sorted(st.order):
        par = parent.get(v)
        if par is None:
            items.append({"node": ids[v], "depth": depth[v], "via": None})
        else:                                         # _via, written out: this loop runs once per listed node
            u, ei, flag = par
            items.append({"node": ids[v], "depth": depth[v], "via": {
                "edge": eids[ei], "relation": names[rel[ei]], "direction": _DIRECTION[flag], "from": ids[u]}})
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
    sides = [_sides(p, fwd, rev) for fwd, rev, _ in positions]
    rel, examined = p._edge_rel, 0
    while queue and st.goal is None:
        k = queue.popleft()
        u, i = divmod(k, stride)
        why = ctl.stop()
        if why:
            st.interruption = why
            break
        tmask, nxt = positions[i][2], i + 1
        for lists, base, marks, ends, flag in sides[i]:
            hs = lists[u]
            if hs is None:
                hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
            examined += len(hs)
            for ei in hs:
                v = ends[ei]
                if tmask is not None and not mask_of[v] & tmask:
                    continue
                nk = v * stride + nxt
                if nk in seen:
                    continue
                seen[nk] = (k, ei, flag)
                if nxt < last:
                    queue.append(nk)
                elif targets is None or v in targets:
                    st.goal = nk
                    examined -= len(hs) - 1 - hs.index(ei)        # the hops after this one were not examined
                    break
            if st.goal is not None:
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
    settled is the answer: no other target costs less, because every cost is non-negative. Distances are held as the
    cost table's search values (integers; in rational mode, counts of ``1/scale``)."""
    dist: dict[int, Any] = {}
    parent: dict[int, tuple[int, int, int]] = {}
    settled: set[int] = set()
    st = SimpleNamespace(goal=None, dist=dist, parent=parent, settled=settled, lower=None, examined=0, discovered=0,
                         interruption=None)
    at_zero = [s for s in plan.sources if s in plan.targets]
    if at_zero:                                   # a source that is a target costs nothing to reach
        st.goal = at_zero[0]
        dist[st.goal] = 0
        st.discovered = len(plan.sources)
        return st
    heap = [(0, s) for s in plan.sources]
    for s in plan.sources:
        dist[s] = 0
    heapq.heapify(heap)
    by_edge, by_rel, rel = plan.cost.edge, plan.cost.rel, p._edge_rel
    sides, targets = _sides(p, plan.fwd, plan.rev), plan.targets
    push, pop = heapq.heappush, heapq.heappop
    examined = 0
    while heap:
        c, u = pop(heap)
        if u in settled or c > dist[u]:
            continue                              # an entry superseded by a cheaper one
        if u in targets:
            st.goal = u
            break
        why = ctl.stop()
        if why:
            st.interruption, st.lower = why, c    # every node not yet settled costs at least c
            break
        settled.add(u)
        for lists, base, marks, ends, flag in sides:
            hs = lists[u]
            if hs is None:
                hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
            examined += len(hs)
            for ei in hs:
                v = ends[ei]
                if v in settled:
                    continue
                nc = c + (by_edge[ei] if by_edge is not None else by_rel[rel[ei]])
                old = dist.get(v)
                if old is None or nc < old:
                    dist[v] = nc
                    parent[v] = (u, ei, flag)
                    push(heap, (nc, v))
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
        value, exact = _number(plan.cost.exact(x), plan.rational)
        return {field: value, **({f"{field}_exact": exact} if plan.rational else {})}

    def witness(t: int) -> dict:
        hops, v = [], t
        while v in st.parent:
            u, ei, flag = st.parent[v]
            hops.append({**_via(p, u, ei, flag), "to": p._node_ids[v], "cost": plan.cost.given(ei)})
            v = u
        hops.reverse()
        return {"source": p._node_ids[v], "target": p._node_ids[t], "length": len(hops), "hops": hops}

    def exact(x: Any) -> Any:
        return _number(plan.cost.exact(x), True)[1] if plan.rational else x

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
    mask_of, rel = p._node_mask, p._edge_rel
    examined = 0
    for s in plan.steps:                                     # 1: discover, downward
        if s.closing:                                        # a cycle's closing step: after the tree is pruned
            found.append({})
            continue
        hops: dict[int, list] = {}
        child = cand[s.child]
        edges, tmask, nodes, fwd = s.edges, s.tmask, s.nodes, s.fwd
        sides = _sides(p, s.fwd, s.rev)
        for u in sorted(cand[s.parent]):
            why = ctl.stop()
            if why:
                st.interruption, st.examined = why, examined
                st.discovered = sum(len(c) for c in cand)
                return st
            kept = []
            for lists, base, marks, ends, flag in sides:
                hs = lists[u]
                if hs is None:
                    hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
                examined += len(hs)
                for ei in hs:
                    w = ends[ei]
                    # a self-loop in a scope of both directions joins u to itself twice by one edge: one row, not two
                    if (edges is None or ei in edges) and (tmask is None or mask_of[w] & tmask) \
                            and (nodes is None or w in nodes) and not (flag and w == u and fwd[rel[ei]]):
                        kept.append((ei, w, flag))
                        child.add(w)
            hops[u] = kept
        found.append(hops)
    st.scanned = [len(c) for c in cand]
    for i in range(len(plan.steps) - 1, -1, -1):              # 2: prune parents, upward
        s = plan.steps[i]
        if not s.optional and not s.closing:
            child = cand[s.child]
            cand[s.parent] = {u for u in cand[s.parent] if any(w in child for _, w, _ in found[i].get(u, ()))}
    kept_hops: list[dict[int, list]] = []
    for i, s in enumerate(plan.steps):                       # 3: prune children, downward
        if s.closing:
            kept_hops.append({})
            continue
        child, reached, hops = cand[s.child], set(), {}
        for u in cand[s.parent]:
            hs = [h for h in found[i].get(u, ()) if h[1] in child]
            hops[u] = hs
            reached.update(w for _, w, _ in hs)
        cand[s.child] = reached
        kept_hops.append(hops)
    st.kept, st.hops, st.examined = cand, kept_hops, examined
    st.discovered = sum(st.scanned)
    if plan.cyclic:
        _enumerate_cycles(p, plan, st, ctl)
    return st


class _Stop(Exception):
    """Ends the enumeration of a cyclic pattern when a limit, a deadline or a cancellation stops it."""


def _enumerate_cycles(p: Projection, plan: SimpleNamespace, st: SimpleNamespace, ctl: _Control) -> None:
    """The rows of a pattern with cycles, by a generic join over the pruned tree (Ngo, Porat, Re and Rudra's worst-
    case optimal join; Veldhuizen's Leapfrog Triejoin).

    Each closing step is indexed first, over the tree's kept candidates: from each candidate of its ``from`` variable,
    its hops in scope that land on a candidate of its ``to`` variable, grouped by that node (one expansion per
    candidate read). Rows are then built step by step in canonical order. When a step binds the later of a closing
    step's two variables, its candidates are intersected with what that closing step allows, iterating the smallest
    of the sets involved and restoring the canonical order of the rest; a closing step then lists its hops between
    the two nodes. Each candidate tried is one expansion. A cycle defeats counting without listing, so every row is
    enumerated: ``total`` is known only when the enumeration ends."""
    steps, kept, rel = plan.steps, st.kept, p._edge_rel
    st.rows_raw, st.total, st.closing_hops = [], None, {}
    index: dict[int, dict[int, dict[int, list]]] = {}        # closing step -> from node -> to node -> hops
    back: dict[int, dict[int, set[int]]] = {}                # closing step -> to node -> from nodes
    examined = st.examined
    try:
        for j, s in enumerate(steps):
            if not s.closing:
                continue
            table, rev, targets = {}, {}, kept[s.child]
            sides = _sides(p, s.fwd, s.rev)
            for u in sorted(kept[s.parent]):
                why = ctl.stop()
                if why:
                    raise _Stop(why)
                by_target: dict[int, list] = {}
                for lists, base, marks, ends, flag in sides:
                    hs = lists[u]
                    if hs is None:
                        hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
                    examined += len(hs)
                    for ei in hs:
                        w = ends[ei]
                        if w in targets and (s.edges is None or ei in s.edges) \
                                and not (flag and w == u and s.fwd[rel[ei]]):
                            by_target.setdefault(w, []).append((ei, w, flag))
                if by_target:
                    table[u] = by_target
                    for w in by_target:
                        rev.setdefault(w, set()).add(u)
            index[j], back[j] = table, rev
            st.closing_hops[j] = sum(len(hs) for by in table.values() for hs in by.values())
    except _Stop as stop:
        st.interruption, st.examined, st.closing_hops = str(stop), examined, None
        return
    st.examined = examined
    bound_at = {0: -1}                                      # where each variable is bound: the match, or a step
    for i, s in enumerate(steps):
        if not s.closing:
            bound_at[s.child] = i
    checks: dict[int, list[tuple[int, str]]] = {}          # where a closing step's later variable is bound
    for j, s in enumerate(steps):
        if s.closing:
            a, b = bound_at[s.parent], bound_at[s.child]
            role = "self" if s.parent == s.child else ("from" if a > b else "to")
            checks.setdefault(max(a, b), []).append((j, role))
    val: list = [None] * len(plan.vars)
    link: list = [None] * len(steps)
    cap = plan.max_results if plan.answer == "rows" else 0
    count = 0
    loops = {j: {u for u, by in index[j].items() if u in by}       # a closing step from a variable to itself
             for j, s in enumerate(steps) if s.closing and s.parent == s.child}
    positions: dict[tuple, dict[int, list]] = {}            # (step, parent node) -> hop target -> positions

    def allowed(j: int, role: str) -> Any:
        """The nodes closing step j allows the variable being bound, given the other one's node."""
        s = steps[j]
        if role == "to":
            return index[j].get(val[s.parent], {})
        if role == "from":
            return back[j].get(val[s.child], ())
        return loops[j]

    def admit(key: tuple, hs: Any, sets: list) -> Any:
        """The hops whose node every set allows, in their canonical order, iterating the smallest side. The hops are
        indexed by target once per step and parent node, so a binding costs the smallest side, not every hop."""
        if not sets:
            return hs
        smallest = min(sets, key=len)
        if len(hs) <= len(smallest):
            return [h for h in hs if all(h[1] in x for x in sets)]
        position = positions.get(key)
        if position is None:
            position = positions[key] = {}
            for k, h in enumerate(hs):
                position.setdefault(h[1], []).append(k)
        picked = [k for w in smallest if w in position and all(w in x for x in sets) for k in position[w]]
        return [hs[k] for k in sorted(picked)]

    def tick() -> None:
        why = ctl.stop()
        if why:
            raise _Stop(why)

    def extend(i: int) -> None:
        nonlocal count
        if i == len(steps):
            count += 1
            if len(st.rows_raw) < cap:
                st.rows_raw.append((tuple(val), tuple(link)))
            return
        s = steps[i]
        if s.closing:
            for ei, _, _ in index[i].get(val[s.parent], {}).get(val[s.child], ()):
                tick()
                link[i] = ei
                extend(i + 1)
            link[i] = None
            return
        u = val[s.parent]
        hs = () if u is None else st.hops[i].get(u, ())
        if checks.get(i):
            hs = admit((i, u), hs, [allowed(j, role) for j, role in checks[i]])
        if not hs:
            if s.optional:                                   # an optional step: no closing step reaches its variable
                val[s.child], link[i] = None, None
                extend(i + 1)
            return
        for ei, w, _ in hs:
            tick()
            val[s.child], link[i] = w, ei
            extend(i + 1)
        val[s.child], link[i] = None, None

    roots = sorted(kept[0])
    if checks.get(-1):
        roots = [h[1] for h in admit((-1,), [(None, u, None) for u in roots], [allowed(j, r) for j, r in checks[-1]])]
    try:
        for u in roots:
            tick()
            val[0] = u
            extend(0)
    except _Stop as stop:
        st.interruption = str(stop)
        st.total = None
        st.found_rows = count
        return
    st.total, st.found_rows = count, count
    if plan.answer == "rows" and count > cap:
        st.interruption = "output_limit"


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
    if plan.cyclic and getattr(st, "closing_hops", None) is None:     # stopped while indexing the cycles
        return {"exists": None, "total": None, "rows": [], "count": 0, "coverage": None}, []
    coverage = {"vars": [{"var": names[i], "candidates": st.scanned[i], "kept": len(st.kept[i])}
                         for i in range(len(names))],
                "steps": [st.closing_hops[i] if s.closing else sum(len(h) for h in st.hops[i].values())
                          for i, s in enumerate(plan.steps)]}
    if plan.cyclic:
        ids, eids, steps = p._node_ids, p._edge_ids, plan.steps
        rows = [{"nodes": {names[i]: (None if v is None else ids[v]) for i, v in enumerate(vals)},
                 "edges": {s.edge_var: (None if links[j] is None else eids[links[j]])
                           for j, s in enumerate(steps) if s.edge_var is not None},
                 "links": [None if x is None else eids[x] for x in links]} for vals, links in st.rows_raw]
        found = getattr(st, "found_rows", 0)
        exists = (st.total > 0) if st.total is not None else (True if found else None)
        answer = {"exists": exists, "total": st.total, "rows": rows, "count": len(rows), "coverage": coverage}
        return answer, ([WITNESS_NOTE] if rows else [])
    total = _pattern_total(plan, st)
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


# ---------------------------------------------------------------------------------------------- bottleneck path
def _search_bottleneck(p: Projection, plan: SimpleNamespace, ctl: _Control) -> SimpleNamespace:
    """The best bottleneck from any source to any target, then the fewest hops among the paths that reach it.

    A path's value is its weakest edge's weight (``max_min``: the higher the better) or its highest (``min_max``: the
    lower the better). Values are compared, never added, so any finite weights are exact.

    1. Nodes are settled best value first, ties by node ID; a hop gives the node it leads to the worse of the two
       values. The first target settled has the best value, and every node settled before it with a strictly
       better value is closed under the hops strictly better than it: none of them is a target.
    2. A breadth-first search over the hops at least as good as that value, from the sources, finds a path with the
       fewest hops among those that reach it, in canonical order."""
    max_min = plan.objective == "max_min"
    combine = min if max_min else max
    start = math.inf if max_min else -math.inf          # the value of a path with no edge
    better = (lambda a, b: a > b) if max_min else (lambda a, b: a < b)
    key = (lambda v: -v) if max_min else (lambda v: v)
    val: dict[int, Any] = {}
    parent: dict[int, tuple[int, int, int]] = {}
    settled: set[int] = set()
    st = SimpleNamespace(goal=None, val=val, parent=parent, settled=settled, bound=None, path=None, better=better,
                         examined=0, discovered=0, interruption=None)
    at_zero = [s for s in plan.sources if s in plan.targets]
    if at_zero:                                   # a source that is a target: the path with no edge
        st.goal, st.path = at_zero[0], []
        st.discovered = len(plan.sources)
        return st
    heap = [(key(start), s) for s in plan.sources]
    for s in plan.sources:
        val[s] = start
    heapq.heapify(heap)
    by_edge, by_rel, rel = plan.weight.edge, plan.weight.rel, p._edge_rel
    sides, targets, examined = _sides(p, plan.fwd, plan.rev), plan.targets, 0
    while heap:                                   # 1: the best value
        k, u = heapq.heappop(heap)
        if u in settled or k != key(val[u]):
            continue                              # an entry superseded by a better one
        if u in targets:
            st.goal = u
            break
        why = ctl.stop()
        if why:                                   # nothing unsettled is better than this (a source: nothing known)
            st.interruption, st.bound = why, (None if val[u] == start else val[u])
            break
        settled.add(u)
        vu = val[u]
        for lists, base, marks, ends, flag in sides:
            hs = lists[u]
            if hs is None:
                hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
            examined += len(hs)
            for ei in hs:
                v = ends[ei]
                if v in settled:
                    continue
                nv = combine(vu, by_edge[ei] if by_edge is not None else by_rel[rel[ei]])
                if v not in val or better(nv, val[v]):
                    val[v] = nv
                    parent[v] = (u, ei, flag)
                    heapq.heappush(heap, (key(nv), v))
    st.discovered = len(val)
    if st.goal is not None:                       # 2: the fewest hops at that value
        best = val[st.goal]
        seen: dict[int, tuple[int, int, int] | None] = {s: None for s in plan.sources}
        queue = deque(plan.sources)
        found = None
        while queue and found is None:
            u = queue.popleft()
            why = ctl.stop()
            if why:                               # the value is known; the fewest hops are not
                st.interruption, st.bound = why, best
                break
            for lists, base, marks, ends, flag in sides:
                hs = lists[u]
                if hs is None:
                    hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
                examined += len(hs)
                for ei in hs:
                    v = ends[ei]
                    if v in seen:
                        continue
                    w = by_edge[ei] if by_edge is not None else by_rel[rel[ei]]
                    if w >= best if max_min else w <= best:
                        seen[v] = (u, ei, flag)
                        if v in targets:
                            found = v
                            examined -= len(hs) - 1 - hs.index(ei)    # the hops after this one were not examined
                            break
                        queue.append(v)
                if found is not None:
                    break
        if found is not None:
            path, v = [], found
            while seen[v] is not None:
                path.append(seen[v] + (v,))
                v = seen[v][0]
            st.path, st.goal = path[::-1], found
    st.examined = examined
    return st


def _answer_bottleneck(p: Projection, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    ids = p._node_ids

    def witness(path: list) -> dict:
        hops = [{**_via(p, u, ei, flag), "to": ids[v], "weight": plan.weight.given(ei)} for u, ei, flag, v in path]
        source = ids[path[0][0]] if path else ids[st.goal]
        return {"source": source, "target": ids[path[-1][3]] if path else ids[st.goal], "length": len(hops),
                "hops": hops}

    def phase_one_path(t: int) -> list:
        path, v = [], t
        while v in st.parent:
            u, ei, flag = st.parent[v]
            path.append((u, ei, flag, v))
            v = u
        return path[::-1]

    def threshold(t: Any) -> dict | None:
        """The settled nodes strictly better than ``t``: closed under the hops strictly better than ``t``, with every
        source and no target, so no path to a target is strictly better than ``t``."""
        if not plan.certificate:
            return None
        return {"kind": "threshold", "threshold": t,
                "reached": [ids[v] for v in sorted(st.settled | set(plan.sources)) if st.better(st.val[v], t)]}

    if st.interruption is None and st.goal is not None:
        if not st.path and st.goal in plan.sources:           # no edge: nothing to prove
            return {"found": True, "optimal": True, "value": None, "witness": witness([]), "certificate": None}, \
                [WITNESS_NOTE]
        value = _path_value(plan, st.path)
        cert = threshold(value)
        return ({"found": True, "optimal": True, "value": value, "witness": witness(st.path), "certificate": cert},
                [WITNESS_NOTE] + ([CERTIFIED_NOTE] if cert else []))
    if st.interruption is None:
        cert = ({"kind": "closure", "reached": [ids[v] for v in sorted(st.val)]} if plan.certificate else None)
        return ({"found": False, "optimal": None, "value": None, "witness": None, "certificate": cert},
                [CERTIFIED_NOTE] if cert else [])
    cert = threshold(st.bound) if st.bound is not None else None
    notes = [CERTIFIED_NOTE] if cert else []
    known = [t for t in plan.targets if t in st.val]
    if st.goal is not None:                       # stopped choosing among the best paths: phase 1's path
        known = [st.goal]
    if not known:
        return {"found": None, "optimal": None, "value": None, "bound": st.bound, "witness": None,
                "certificate": cert}, notes
    t = min(known, key=lambda x: (-st.val[x], x) if plan.objective == "max_min" else (st.val[x], x))
    path = phase_one_path(t)
    return ({"found": True, "optimal": False, "value": _path_value(plan, path), "bound": st.bound,
             "witness": witness(path), "certificate": cert}, [TENTATIVE_NOTE, WITNESS_NOTE] + notes)


def _path_value(plan: SimpleNamespace, path: list) -> Any:
    weights = [plan.weight.given(ei) for _, ei, _, _ in path]
    return (min if plan.objective == "max_min" else max)(weights)


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

    rel = p._edge_rel
    region, queue = set(failed), deque(plan.failed)          # 1: the failed nodes and their dependents
    sides = _back_sides(p, fwd, rev)
    while queue:
        v = queue.popleft()
        if stopped():
            st.discovered = len(region)
            return st
        for lists, base, marks, ends, _ in sides:
            hs = lists[v]
            if hs is None:
                hs = lists[v] = [ei for ei in base[v] if marks[rel[ei]]]
            examined += len(hs)
            for ei in hs:
                u = ends[ei]
                if u not in region:
                    region.add(u)
                    queue.append(u)
    supports: dict[int, list[tuple[int, int, int]]] = {}     # 2: their support closure, each node's supports once
    closure, queue = set(region), deque(sorted(region))
    sides = _sides(p, fwd, rev)
    while queue:
        u = queue.popleft()
        if stopped():
            st.discovered = len(closure)
            return st
        sup, seen = [], set()
        for lists, base, marks, ends, flag in sides:
            hs = lists[u]
            if hs is None:
                hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
            examined += len(hs)
            for ei in hs:
                v = ends[ei]
                if v not in seen:                             # parallel hops to one support: one support
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


# ---------------------------------------------------------------------------------------------- regular path
def _search_regular(p: Projection, plan: SimpleNamespace, ctl: _Control) -> SimpleNamespace:
    """Breadth-first search over product states (node, automaton state) of the path's Glushkov automaton
    (:func:`graph_runtime.paths.automaton`), from every source at the start state: a hop from (u, q) to v moves to
    (v, i) for every state i that follows q and whose step the hop matches. Each product state is expanded once (a
    state with no moves is not expanded), so the search ends; its first discovery has the fewest hops.

    ``witness``: stop at the first accepting state at a target (any node when targets is null); a source that is a
    target matches with no hop when the path matches the empty walk. ``reached``: list every node (among the targets)
    at which an accepting state is reached, with its fewest hops; without a certificate, stop once every target is
    listed."""
    auto, states = plan.auto, plan.states
    stride, follow, accepting = len(auto.steps), auto.follow, auto.accepting
    targets, cap, witness = plan.targets, plan.max_results, plan.answer == "witness"
    seen: dict[int, tuple[int, int, int] | None] = {}
    length: dict[int, int] = {}
    reached: dict[int, int] = {}                          # node -> its first accepting state (key), in order found
    st = SimpleNamespace(goal=None, seen=seen, length=length, reached=reached, stride=stride, examined=0,
                         discovered=0, interruption=None)
    for s in plan.sources:
        seen[s * stride] = None
        length[s * stride] = 0
    st.discovered = len(seen)
    if auto.nullable:                                     # the empty walk matches: a source is its own end
        for s in plan.sources:
            if targets is None or s in targets:
                if witness:
                    st.goal = s * stride
                    return st
                if len(reached) >= cap:
                    st.interruption = "output_limit"
                    return st
                reached[s] = s * stride
    queue = deque(s * stride for s in plan.sources) if follow[0] else deque()
    early = not witness and targets is not None and not plan.certificate
    if early and len(reached) == len(targets):
        queue.clear()
    mask_of, rel, examined, done = p._node_mask, p._edge_rel, 0, False
    while queue and not done:
        k = queue.popleft()
        u, q = divmod(k, stride)
        why = ctl.stop()
        if why:
            st.interruption = why
            break
        sides, by_relation = states[q]
        nl = length[k] + 1
        for (lists, base, marks, ends, flag), matches in zip(sides, by_relation):
            hs = lists[u]
            if hs is None:
                hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
            examined += len(hs)
            for ei in hs:
                v = ends[ei]
                for i, tmask in matches[rel[ei]]:
                    if tmask is not None and not mask_of[v] & tmask:
                        continue
                    nk = v * stride + i
                    if nk in seen:
                        continue
                    ends_here = i in accepting and (targets is None or v in targets)
                    if ends_here and not witness and v not in reached and len(reached) >= cap:
                        st.interruption, done = "output_limit", True
                        break
                    seen[nk] = (k, ei, flag)
                    length[nk] = nl
                    if ends_here:
                        if witness:
                            st.goal, done = nk, True
                            break
                        if v not in reached:
                            reached[v] = nk
                            if early and len(reached) == len(targets):
                                done = True
                                break
                    if follow[i]:
                        queue.append(nk)
                if done:
                    examined -= len(hs) - 1 - hs.index(ei)        # the hops after this one were not examined
                    break
            if done:
                break
    st.examined, st.discovered = examined, len(seen)
    return st


def _answer_regular(p: Projection, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    ids, stride, seen, length = p._node_ids, st.stride, st.seen, st.length

    def certificate(bound: int | None) -> dict:
        """Every product state found with fewer than ``bound`` hops (all, when null), by state then node, with its
        fewest hops: closed under the path's moves below the bound, so no walk the states do not list matches."""
        keys = sorted((k for k in seen if bound is None or length[k] < bound), key=lambda k: (k % stride, k // stride))
        return {"kind": "product_states", "bound": bound,
                "states": [{"node": ids[k // stride], "state": k % stride, "length": length[k]} for k in keys]}

    complete = st.interruption is None
    if plan.answer == "witness":
        if st.goal is None:
            cert = certificate(None) if plan.certificate and complete else None
            return ({"found": None if st.interruption else False, "witness": None, "certificate": cert},
                    [CERTIFIED_NOTE] if cert else [])
        hops, k = [], st.goal
        while seen[k] is not None:
            pk, ei, flag = seen[k]
            hops.append({**_via(p, pk // stride, ei, flag), "to": ids[k // stride], "state": k % stride})
            k = pk
        hops.reverse()
        cert = certificate(len(hops)) if plan.certificate and hops else None
        witness = {"source": ids[k // stride], "target": ids[st.goal // stride], "length": len(hops), "hops": hops}
        return ({"found": True, "witness": witness, "certificate": cert},
                [WITNESS_NOTE] + ([CERTIFIED_NOTE] if cert else []))
    tree: set[int] = set()
    for nk in st.reached.values():                      # the branches of the search tree that witness the answer
        while nk is not None and nk not in tree:
            tree.add(nk)
            nk = seen[nk][0] if seen[nk] is not None else None
    entries = []
    for k in sorted(tree, key=lambda k: (k // stride, k % stride)):
        par = seen[k]
        via = None
        if par is not None:
            pk, ei, flag = par
            via = {**_via(p, pk // stride, ei, flag), "from_state": pk % stride}
        entries.append({"node": ids[k // stride], "state": k % stride, "length": length[k], "via": via})
    listed = [{"node": ids[v], "length": length[k], "state": k % stride} for v, k in sorted(st.reached.items())]
    cert = certificate(None) if plan.certificate and complete else None
    return ({"reached": listed, "count": len(listed), "tree": entries, "certificate": cert},
            ([WITNESS_NOTE] if listed else []) + ([CERTIFIED_NOTE] if cert else []))


# ---------------------------------------------------------------------------------------------- derivation
class _Objective:
    """How a derivation's value is made from its hops' costs, for one objective (Knuth's superior functions: every
    combination is at least as bad as each of its parts, so the best values can be settled in order).

    ``min_sum``: a derivation's value is the sum of its hops' costs, every use counted (a tree, not a DAG); lower is
    better; a given node is worth 0. ``min_max``: the highest cost of its hops; lower is better. ``max_min``: the
    lowest; higher is better. For the last two a given node is worth ``IDENTITY`` (no hop: written null), better than
    any number. ``hop(c, v)`` is the value of using an input worth v through a hop of cost c; ``fold(a, b)`` combines
    the inputs of an all_of node, in the order its uses are listed (on a tie, the earlier one is kept, so a reported
    value is reproducible from the listed uses alone)."""

    IDENTITY = None

    def __init__(self, name: str):
        self.name = name
        self.sum = name == "min_sum"
        self.high = name == "max_min"                     # higher is better
        self.identity = 0 if self.sum else None

    def better(self, a: Any, b: Any) -> bool:
        """Whether value a is strictly better than value b (None: no hop, the best there is)."""
        if self.sum:
            return a < b
        if a is None or b is None:
            return a is None and b is not None
        return a > b if self.high else a < b

    def hop(self, c: Any, v: Any) -> Any:
        if self.sum:
            return c + v
        if v is None:
            return c
        return v if (v <= c if self.high else v >= c) else c

    def fold(self, a: Any, b: Any) -> Any:
        if self.sum:
            return a + b
        if a is None:
            return b
        if b is None:
            return a
        return a if (a <= b if self.high else a >= b) else b

    def key(self, v: Any) -> Any:
        """A heap key: smaller is settled first."""
        if self.sum:
            return v
        if v is None:
            return (0, 0)
        return (1, -v if self.high else v)


def _search_derivation(p: Projection, plan: SimpleNamespace, ctl: _Control) -> SimpleNamespace:
    """Lightest derivations in an AND/OR graph (Knuth's generalization of Dijkstra's algorithm, 1977; Felzenszwalb and
    McAllester's lightest derivation problem). A hop in scope leads from a node to one of its inputs. A given node
    (a ground) needs nothing. A node of an ``all_of`` type is derived from every one of its inputs (one hop each, the
    best of parallel hops); any other node from one input. Derivations are finite, so support in a circle derives
    nothing.

    1. The region: the targets and, transitively, their inputs, stopping at grounds. Each node is read once, keeping
       for each distinct input its best hop (the first in canonical order among equals).
    2. Settle, best value first (ties by node ID): grounds and all_of nodes without inputs start at the identity; a
       settled node offers its value to the nodes that use it. A one-input node takes a strictly better offer; an
       all_of node is ready, with every input combined, when its last input settles. A settled value is final and
       the best there is; the search stops when every target is settled or nothing is left to settle."""
    obj = _Objective(plan.objective)
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, phase="region", obj=obj, bound=None,
                         settled=[], value={}, choice={}, inputs={}, ground=set())
    fwd, rev, rel = plan.fwd, plan.rev, p._edge_rel
    every, mask_of, grounds = plan.all_of, p._node_mask, plan.grounds
    by_edge = plan.cost.edge if plan.cost is not None else None
    by_rel = plan.cost.rel if plan.cost is not None else None
    sides = _sides(p, fwd, rev)
    examined = 0
    inputs = st.inputs                                    # node -> [(input, cost, edge, flag)], its best hop to each
    region, queue = set(plan.targets), deque(plan.targets)
    while queue:                                          # 1: the region
        u = queue.popleft()
        if grounds is not None and u in grounds:
            inputs[u] = []
            st.ground.add(u)
            continue
        why = ctl.stop()
        if why:
            st.interruption, st.examined, st.discovered = why, examined, len(region)
            return st
        best: dict[int, tuple] = {}
        for lists, base, marks, ends, flag in sides:
            hs = lists[u]
            if hs is None:
                hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
            examined += len(hs)
            for ei in hs:
                v = ends[ei]
                c = by_edge[ei] if by_edge is not None else by_rel[rel[ei]] if by_rel is not None else 1
                b = best.get(v)
                if b is None or (c > b[0] if obj.high else c < b[0]):
                    best[v] = (c, ei, flag)
                if v not in region:
                    region.add(v)
                    queue.append(v)
        inputs[u] = [(v, c, ei, flag) for v, (c, ei, flag) in best.items()]
        if not inputs[u] and grounds is None and not (every is not None and mask_of[u] & every):
            st.ground.add(u)                              # no input, needs one: given
    st.discovered = len(region)
    st.phase = "derive"
    users: dict[int, list] = {u: [] for u in region}
    for u, ins in inputs.items():
        for v, c, _, _ in ins:
            users[v].append((u, c))
    every_input = {u for u in region if u not in st.ground and every is not None and mask_of[u] & every}
    need = {u: len(inputs[u]) for u in every_input}
    acc: dict[int, Any] = {}
    value, choice, settled = st.value, st.choice, st.settled
    done: set[int] = set()
    heap = []
    for u in sorted(region):
        if u in st.ground or (u in every_input and not need[u]):
            value[u] = obj.identity
            heap.append((obj.key(obj.identity), u))
    heapq.heapify(heap)
    waiting = set(plan.targets)
    while heap and waiting:                               # 2: settle, best first
        k, u = heapq.heappop(heap)
        if u in done or k != obj.key(value[u]):
            continue                                      # settled, or an entry superseded by a better one
        why = ctl.stop()
        if why:
            st.interruption, st.bound = why, value[u]     # nothing unsettled is better than this
            break
        done.add(u)
        settled.append(u)
        waiting.discard(u)
        vu = value[u]
        for d, c in users[u]:
            examined += 1
            if d in done or d in st.ground:
                continue
            x = obj.hop(c, vu)
            if d in every_input:
                acc[d] = x if d not in acc else obj.fold(acc[d], x)
                need[d] -= 1
                if not need[d]:
                    value[d] = acc[d]
                    heapq.heappush(heap, (obj.key(acc[d]), d))
            elif d not in value or obj.better(x, value[d]):
                value[d] = x
                choice[d] = u
                heapq.heappush(heap, (obj.key(x), d))
    if st.interruption is None:                           # the best value still unsettled bounds the rest
        while heap and (heap[0][1] in done or heap[0][0] != obj.key(value[heap[0][1]])):
            heapq.heappop(heap)
        st.bound = value[heap[0][1]] if heap else None
        st.rest = bool(heap)
    st.examined, st.done, st.every_input = examined, done, every_input
    return st


def _answer_derivation(p: Projection, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    ids, obj, rational = p._node_ids, st.obj, plan.rational

    def number(field: str, x: Any) -> dict:
        """A value as JSON gives it: a sum is an integer, or in rational mode the nearest float and ``_exact`` (both
        null for no value)."""
        if not obj.sum or not rational:
            return {field: x}
        if x is None:
            return {field: None, f"{field}_exact": None}
        near, exact = _number(plan.cost.exact(x), True)
        return {field: near, f"{field}_exact": exact}

    if st.phase == "region":                              # stopped while finding the region: nothing is known
        return {"derived": [], "underived": [], **number("bound", None), "derivation": [], "certificate": None}, []
    done, inputs = st.done, st.inputs
    order = {u: i for i, u in enumerate(st.settled)}
    uses: dict[int, list] = {}                            # node -> its uses (edge, flag, input, cost), canonical order
    rules: dict[int, str] = {}
    stack = [t for t in plan.targets if t in done]
    while stack:                                          # the derivations of the derived targets
        u = stack.pop()
        if u in uses:
            continue
        if u in st.ground:
            rules[u], uses[u] = "given", []
        elif u in st.every_input:
            rules[u], uses[u] = "all", sorted(((ei, flag, v, c) for v, c, ei, flag in inputs[u]),
                                              key=lambda h: (h[1], h[0]))
        else:
            v = st.choice[u]
            rules[u], uses[u] = "one", [next((ei, flag, w, c) for w, c, ei, flag in inputs[u] if w == v)]
        stack.extend(h[2] for h in uses[u])
    worth: dict[int, Any] = {}                            # each value, recomputed from the listed uses alone
    for u in sorted(uses, key=order.__getitem__):        # inputs settle before the nodes that use them
        acc = obj.identity
        for n, (_, _, v, c) in enumerate(uses[u]):
            x = obj.hop(c, worth[v])
            acc = x if n == 0 else obj.fold(acc, x)
        worth[u] = acc
    given = plan.cost.given if plan.cost is not None else (lambda ei: 1)
    derivation = [{"node": ids[u], "rule": rules[u], **number("value", worth[u]),
                   "uses": [{**_via(p, u, ei, flag), "to": ids[v], "cost": given(ei)} for ei, flag, v, _ in uses[u]]}
                  for u in sorted(uses)]
    complete = st.interruption is None
    answer: dict = {"derived": [{"node": ids[t], **number("value", worth[t])} for t in plan.targets if t in done],
                    "underived": [ids[t] for t in plan.targets if t not in done] if complete else []}
    if not complete:                                      # no unsettled target is better (null: nothing is known)
        answer.update(number("bound", None if st.bound == obj.identity else st.bound))
    answer["derivation"] = derivation
    cert = None
    if plan.certificate and (complete or st.bound != obj.identity):
        bound = (st.bound if st.rest else None) if complete else st.bound
        cert = {"kind": "values", **number("bound", bound),
                "values": [{"node": ids[u], **number("value", st.value[u])} for u in sorted(done)]}
    notes = ([WITNESS_NOTE] if derivation else []) + ([CERTIFIED_NOTE] if cert else [])
    return {**answer, "certificate": cert}, notes


_OPERATIONS = {"reachability": (_search_reachability, _answer_reachability),
               "dependency_impact": (_search_impact, _answer_impact),
               "typed_path": (_search_typed, _answer_typed),
               "shortest_path": (_search_shortest, _answer_shortest),
               "pattern": (_search_pattern, _answer_pattern),
               "support": (_search_support, _answer_support),
               "bottleneck_path": (_search_bottleneck, _answer_bottleneck),
               "regular_path": (_search_regular, _answer_regular),
               "derivation": (_search_derivation, _answer_derivation),
               "personalized_pagerank": (lambda p, plan, ctl: ranking.search(p, plan, ctl),
                                         lambda p, plan, st: ranking.answer(p, plan, st)),
               "components": (lambda p, plan, ctl: structure.search_components(p, plan, ctl),
                              lambda p, plan, st: structure.answer_components(p, plan, st)),
               "biconnectivity": (lambda p, plan, ctl: structure.search_biconnectivity(p, plan, ctl),
                                  lambda p, plan, st: structure.answer_biconnectivity(p, plan, st)),
               "max_flow": (lambda p, plan, ctl: flows.search_flow(p, plan, ctl),
                            lambda p, plan, st: flows.answer_flow(p, plan, st)),
               "matching": (lambda p, plan, ctl: flows.search_matching(p, plan, ctl),
                            lambda p, plan, st: flows.answer_matching(p, plan, st)),
               "dominators": (lambda p, plan, ctl: dominators.search(p, plan, ctl),
                              lambda p, plan, st: dominators.answer(p, plan, st)),
               "incidence": (lambda p, plan, ctl: algebra.search(p, plan, ctl),
                             lambda p, plan, st: algebra.answer(p, plan, st)),
               "critical_path": (lambda p, plan, ctl: schedule.search(p, plan, ctl),
                                 lambda p, plan, st: schedule.answer(p, plan, st)),
               "d_separation": (lambda p, plan, ctl: causal.search(p, plan, ctl),
                                lambda p, plan, st: causal.answer(p, plan, st))}
