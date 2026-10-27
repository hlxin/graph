"""Minimum-cost flows: send a given amount (or as much as possible) from the sources to the targets at least cost.

Each hop in scope carries at most its edge's capacity at its edge's cost per unit (an edge in scope both ways carries
flow either way, up to its capacity each way, as for a maximum flow). Transport and supply chains, the cheapest routes
for a total throughput, the least-cost way to feed a set of sinks: the value to send is ``value``, or, when null, the
maximum the capacities allow. Capacities and costs are exact (integers, or floats as exact rationals over common
power-of-two scales); costs are non-negative.

Successive shortest paths (Jewell, 1958; Busacker and Gowen, 1960; with potentials, Edmonds and Karp, 1972, and
Tomizawa, 1971): a virtual source feeds the sources and a virtual sink drains the targets; each phase is Dijkstra's
search from the virtual source over the residual network, costs reduced by node potentials so that none is negative,
then an augmentation along the cheapest path by as much as it carries (or as much as is still wanted), and the
potentials rise by the search's distances. The flow of each value reached this way is a cheapest one of that value.

The certificate is the potentials (and, when the flow is a maximum one, the source side of a minimum cut): every arc of
the final residual network costs nothing negative once reduced, so no cycle there costs less than nothing and no flow
of the same value is cheaper; and every hop out of the cut's source side is full and every hop into it empty, so its
capacity is the value and no flow is larger.
"""

from __future__ import annotations

import heapq
from fractions import Fraction
from types import SimpleNamespace
from typing import Any

_DIRECTION = ("forward", "reverse")


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """The residual network (every node's hops in scope read once, one expansion each), then successive shortest
    paths, each settled state of each Dijkstra search one expansion. An interrupted search keeps the flow sent: a
    valid flow, never claimed cheapest or largest."""
    from .engine import _sides
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, sent=0, head=[], res=[], cap=[], cost=[],
                         arc_edge=[], pi=None, side=None, maximum=None)
    sides, rel = _sides(p, plan.fwd, plan.rev), p._edge_rel
    n = len(p._node_ids)
    source, sink = n, n + 1
    head, res, cap, cost, arc_edge = st.head, st.res, st.cap, st.cost, st.arc_edge
    adj: list[list[int]] = [[] for _ in range(n + 2)]

    def arc(x: int, y: int, c: int, w: int, info: Any) -> None:
        for frm, to, amount, price, tag in ((x, y, c, w, info), (y, x, 0, -w, None)):
            adj[frm].append(len(head))
            head.append(to)
            res.append(amount)
            cap.append(amount)
            cost.append(price)
            arc_edge.append(tag)
    for u in range(n):
        why = ctl.stop()
        if why:
            st.interruption = why
            return st
        for lists, base, marks, ends, flag in sides:
            hs = lists[u]
            if hs is None:
                hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
            st.examined += len(hs)
            for ei in hs:
                v = ends[ei]
                if v != u:                                # a loop carries nothing anywhere
                    arc(u, v, plan.capacity(ei), plan.cost(ei), (ei, flag))
    unlimited = sum(cap) + 1                              # more than every hop together carries
    for s in plan.sources:
        arc(source, s, unlimited, 0, None)
    for t in plan.targets:
        arc(t, sink, unlimited, 0, None)
    wanted = plan.value                                   # None: as much as possible
    pi = [0] * (n + 2)                                    # costs are non-negative: every reduced cost is too
    st.pi = pi
    st.discovered = n
    while wanted is None or st.sent < wanted:
        dist: dict[int, int] = {source: 0}
        prev: dict[int, int] = {}
        done: set[int] = set()
        heap = [(0, source)]
        while heap:
            d, x = heapq.heappop(heap)
            if x in done:
                continue
            why = ctl.stop()
            if why:
                st.interruption = why
                return st
            done.add(x)
            if x == sink:
                break
            for k in adj[x]:
                if res[k]:
                    y = head[k]
                    nd = d + cost[k] + pi[x] - pi[y]
                    if y not in done and (y not in dist or nd < dist[y]):
                        dist[y], prev[y] = nd, k
                        heapq.heappush(heap, (nd, y))
        if sink not in done:
            st.maximum = True
            st.side = done                                # what the virtual source reaches: a minimum cut's side
            return st
        top = dist[sink]
        for x in range(n + 2):
            pi[x] += dist[x] if x in done else top
        push, y = None, sink
        while y != source:
            k = prev[y]
            push = res[k] if push is None else min(push, res[k])
            y = head[k ^ 1]
        if wanted is not None:
            push = min(push, wanted - st.sent)
        y = sink
        while y != source:
            k = prev[y]
            res[k] -= push
            res[k ^ 1] += push
            y = head[k ^ 1]
        st.sent += push
    st.maximum = False
    return st


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    from .engine import _number
    ids, eids, names, rel, src, dst = (p._node_ids, p._edge_ids, p._relations, p._edge_rel, p._edge_src,
                                       p._edge_dst)

    def number(field: str, x: Any, scale: int) -> dict:
        value, exact = _number(Fraction(x, scale) if plan.rational else x, plan.rational)
        return {field: value, **({f"{field}_exact": exact} if plan.rational else {})}

    def exact(x: Any) -> Any:
        return _number(Fraction(x, plan.cost_scale), True)[1] if plan.rational else x
    flows, total = [], 0
    for k in range(0, len(st.head), 2):
        info = st.arc_edge[k]
        moved = st.cap[k] - st.res[k]
        if info is not None and moved:
            ei, flag = info
            a, b = (src[ei], dst[ei]) if flag == 0 else (dst[ei], src[ei])
            flows.append({"edge": eids[ei], "relation": names[rel[ei]], "direction": _DIRECTION[flag],
                          "from": ids[a], "to": ids[b], **number("flow", moved, plan.cap_scale)})
            total += moved * st.cost[k]
    flows.sort(key=lambda f: (f["edge"], f["direction"]))
    complete = st.interruption is None
    requested = plan.value
    answer: dict = {**number("value", st.sent, plan.cap_scale),
                    "feasible": (requested is None or st.sent >= requested) if complete else None,
                    **number("cost", total, plan.cap_scale * plan.cost_scale), "optimal": complete, "flows": flows}
    cert = None
    if complete and plan.certificate:
        n = len(ids)
        touched = sorted({x for k in range(0, len(st.head), 2) for x in (st.head[k], st.head[k ^ 1]) if x < n})
        cert = {"kind": "potentials", "potentials": [{"node": ids[x], "value": exact(st.pi[x])} for x in touched],
                "source": exact(st.pi[n]), "sink": exact(st.pi[n + 1]),
                "source_side": [ids[x] for x in sorted(v for v in st.side if v < n)] if st.maximum else None}
    answer["certificate"] = cert
    return answer, [FLOW_NOTE] if complete else []


FLOW_NOTE = ("The flow is a cheapest one of its value; when it is all the capacities allow (feasible false, or no "
             "value asked), it is a maximum flow too. The certificate's potentials leave no residual arc negative once "
             "reduced, and its cut's capacity is the value.")
