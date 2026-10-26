"""Critical paths: the order a directed acyclic graph allows, its longest paths, and how late each node can come.

Read as precedence (a hop from u to v: u comes before v, the hop's cost the time it takes), a directed acyclic graph
is a schedule: a node's earliest time is its longest path from a node nothing precedes, the makespan the longest of
these, and a critical path one that long (Kelley and Walker's critical path method, 1959). A node's latest time is
the makespan less its longest path to a node nothing follows; its slack, latest less earliest, is how far it can slip
without delaying the whole; the critical nodes have none. Build systems, workflows, the depth of a derivation or
of a provenance chain, reaction steps in sequence: what must come first, and what decides the total. A graph with a
cycle has no such order, and the answer gives a cycle instead.

The order is Kahn's (1962), taking the smallest node first among those whose predecessors are all placed: the
lexicographically smallest topological order, canonical. Costs are exact (integers, or floats as exact rationals);
without costs every hop takes 1, so earliest times are layers. With sources, only what they reach is scheduled.

The certificate is that order with each node's earliest time: every hop goes forward in it, and the times satisfy
the longest-path recurrence exactly, which in an acyclic graph has one solution, so a checker proves the makespan and
the critical path from them, hop by hop. A cycle needs no certificate: its hops are its proof.
"""

from __future__ import annotations

import heapq
from types import SimpleNamespace
from typing import Any

_DIRECTION = ("forward", "reverse")


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """The region (every node, or what the sources reach), each node's hops read once (one expansion), then Kahn's
    order over it; with every node placed, earliest times forward; otherwise a cycle among the nodes left. Ordering
    and the cycle take time proportional to what was read. An interrupted search claims nothing."""
    from .engine import _back_sides, _sides
    from .structure import _neighbors
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, order=None, cycle=None, reach=None)
    sides, back = _sides(p, plan.fwd, plan.rev), _back_sides(p, plan.fwd, plan.rev)
    n = len(p._node_ids)
    parent: dict[int, tuple] = {}
    succ: dict[int, list] = {}
    region = set(range(n)) if plan.sources is None else set(plan.sources)
    queue = list(range(n)) if plan.sources is None else list(plan.sources)
    for u in queue:
        why = ctl.stop()
        if why:
            st.interruption = why
            return st
        hops = succ[u] = _neighbors(p, sides, u)
        if plan.sources is not None:
            for ei, v, flag in hops:
                if v not in region:
                    region.add(v)
                    parent[v] = (u, ei, flag)
                    queue.append(v)
    st.discovered = len(region)
    pred = {v: [(ei, u, flag) for ei, u, flag in _neighbors(p, back, v) if u in region] for v in region}
    st.examined = sum(len(x) for x in succ.values())
    need = {v: len(pred[v]) for v in region}
    ready = [v for v in region if need[v] == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        u = heapq.heappop(ready)
        order.append(u)
        for _, v, _ in succ[u]:
            need[v] -= 1
            if need[v] == 0:
                heapq.heappush(ready, v)
    if len(order) < len(region):                     # a cycle among the nodes never placed
        left = {v for v in region if need[v] > 0}
        at, seen, walk = min(left), {}, []
        while at not in seen:                        # walk backwards: every node left has a predecessor left
            seen[at] = len(walk)
            ei, u, flag = next(h for h in pred[at] if h[1] in left)
            walk.append((u, ei, flag, at))
            at = u
        loop = walk[seen[at]:]
        loop.reverse()                               # forward: each hop from u to the next node
        st.cycle = loop
        if plan.sources is not None:
            path, v = [], loop[0][0]
            while v in parent:
                u, ei, flag = parent[v]
                path.append((u, ei, flag, v))
                v = u
            st.reach = path[::-1]
        return st
    cost = plan.cost_of
    earliest, best = {}, {}
    for v in order:
        top, choice = 0, None
        for ei, u, flag in pred[v]:
            t = earliest[u] + cost(ei)
            if choice is None or t > top:
                top, choice = t, (u, ei, flag)
        earliest[v], best[v] = top, choice
    st.order, st.earliest, st.best, st.succ = order, earliest, best, succ
    return st


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    """The makespan (``cost``) and a critical path to the smallest node that ends one; with ``schedule``, every node's
    earliest and latest time and slack by node ID; on request, the certificate. A cyclic graph: a cycle (and, with
    sources, the hops that reach it from one)."""
    from .engine import _number
    ids, eids, names, rel = p._node_ids, p._edge_ids, p._relations, p._edge_rel

    def hop(u: int, ei: int, flag: int, v: int) -> dict:
        h = {"edge": eids[ei], "relation": names[rel[ei]], "direction": _DIRECTION[flag], "from": ids[u], "to": ids[v]}
        if plan.costs is not None:
            h["cost"] = plan.given(ei)
        return h

    def number(field: str, x: Any) -> dict:
        value, exact = _number(plan.exact(x), plan.rational)
        return {field: value, **({f"{field}_exact": exact} if plan.rational else {})}
    out: dict = {"acyclic": None, "cycle": None, "cost": None, "path": None, "schedule": None, "certificate": None}
    if st.interruption:
        return out, []
    if st.cycle is not None:
        out.update(acyclic=False, cycle={"hops": [hop(*h) for h in st.cycle],
                                         "reach": None if st.reach is None else [hop(*h) for h in st.reach]})
        return out, []
    earliest = st.earliest
    span = max(earliest.values(), default=0)
    out.update(acyclic=True, **number("cost", span))
    if earliest:
        end = min(v for v in st.order if earliest[v] == span)
        hops, v = [], end
        while st.best[v] is not None:
            u, ei, flag = st.best[v]
            hops.append(hop(u, ei, flag, v))
            v = u
        hops.reverse()
        out["path"] = {"source": ids[v], "target": ids[end], "length": len(hops), "hops": hops}
    if plan.schedule:
        latest = {}
        for v in reversed(st.order):
            latest[v] = min((latest[w] - plan.cost_of(ei) for ei, w, _ in st.succ[v]), default=span)
        out["schedule"] = [{"node": ids[v], **number("earliest", earliest[v]), **number("latest", latest[v]),
                            **number("slack", latest[v] - earliest[v])} for v in sorted(earliest)]
    if plan.certificate:
        out["certificate"] = {"kind": "topological", "order": [{"node": ids[v], **number("earliest", earliest[v])}
                                                               for v in st.order]}
    return out, []
