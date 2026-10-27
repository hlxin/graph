"""Minimum cycle bases: the rings of a molecule, the independent loops of a network, with the least total weight.

Read undirected (each edge in scope a link between its ends, whichever way the scope follows it), the links form a
multigraph whose cycle space, over GF(2), has dimension m - n + c (links, nodes, connected pieces): a loop is a cycle
of one link, two parallel links a cycle of two. A cycle basis is that many independent simple cycles; a minimum one
has the least total weight (with every link 1, the fewest links: the smallest set of smallest rings, SSSR, of
chemistry, which is one minimum basis among possibly several). Weights are non-negative and exact.

De Pina's algorithm (1995; Kavitha, Mehlhorn, Michail and Paluch, 2004): with the links outside a spanning forest
(by edge ID) as coordinates e_1..e_k and witnesses S_i = e_i to start, cycle i is a lightest cycle whose intersection
with S_i is odd, and every later witness that meets it oddly takes S_i in; so S_i is orthogonal to cycles 1..i-1. A
lightest S-odd cycle is a shortest path from (v, 0) to (v, 1) in the parity graph (two copies of the links, a link of
S crossing between them), from each end v of a link of S (every S-odd cycle passes one), the walk read mod 2.

De Pina's theorem makes the witnesses a proof: the cycles are independent (cycle i meets S_i oddly and every earlier
cycle evenly), and if each is a lightest cycle meeting its witness oddly, the basis is minimum. A checker recomputes
those lightest weights itself. Each cycle is final once found, so an interrupted search lists a proven prefix.
"""

from __future__ import annotations

import heapq
from types import SimpleNamespace
from typing import Any


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """The links (each node's edges read once, one expansion), a spanning forest by edge ID, then de Pina's cycles,
    each from its parity searches, one expansion per state settled. An interruption keeps the cycles found."""
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, cycles=[], witnesses=[], rank=None)
    rel, src, dst, fwd, rev = p._edge_rel, p._edge_src, p._edge_dst, plan.fwd, plan.rev
    n = len(p._node_ids)
    links: list[int] = []
    for u in range(n):
        why = ctl.stop()
        if why:
            st.interruption = why
            return st
        out = p._out[u]
        st.examined += len(out)
        links.extend(ei for ei in out if fwd[rel[ei]] or rev[rel[ei]])
    links.sort()
    up = list(range(n))

    def root(x: int) -> int:
        while up[x] != x:
            up[x] = up[up[x]]
            x = up[x]
        return x
    outside = []
    for ei in links:                                      # a spanning forest by edge ID; the rest are coordinates
        a, b = root(src[ei]), root(dst[ei])
        if a == b:
            outside.append(ei)
        else:
            up[a] = b
    st.rank = len(outside)
    adjacent: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for ei in links:
        adjacent[src[ei]].append((ei, dst[ei]))
        if src[ei] != dst[ei]:
            adjacent[dst[ei]].append((ei, src[ei]))
    weight = plan.weight
    witnesses = [{ei} for ei in outside]                  # sets of edge indices: sparse, as long as they stay small
    holders: dict[int, set[int]] = {ei: {j} for j, ei in enumerate(outside)}     # edge -> the witnesses holding it
    st.discovered = n
    try:
        for i in range(len(outside)):
            s = witnesses[i]
            bits, w = _lightest_odd(s, adjacent, src, dst, weight, ctl)
            members = set(_members(bits))
            st.cycles.append((bits, w))
            st.witnesses.append(sorted(s))
            # a later witness meets the cycle oddly only if it holds one of its edges: those take this one in
            for j in sorted({j for e in members for j in holders.get(e, ()) if j > i}):
                if len(witnesses[j] & members) & 1:
                    for e in s:
                        held = holders.setdefault(e, set())
                        held.symmetric_difference_update((j,))
                    witnesses[j] ^= s
    except _Stop as stop:
        st.interruption = str(stop)
    return st


class _Stop(Exception):
    """A limit, a deadline or a cancellation stopped a parity search."""


def _lightest_odd(s: set, adjacent: list, src: Any, dst: Any, weight: Any, ctl: Any) -> tuple[int, Any]:
    """(edge bitset, weight) of a lightest simple cycle whose intersection with ``s`` is odd: the least over the
    starts v (an end of each link of ``s``, ascending) of a shortest path from (v, 0) to (v, 1), states settled by
    (distance, node, parity), each settled state one expansion; its walk mod 2, an even subgraph meeting ``s``
    oddly, then split into simple cycles, of which the first to meet ``s`` oddly is kept (it weighs no more, so as
    little)."""
    starts = sorted({src[ei] for ei in s})
    best: tuple[Any, int] | None = None
    for start in starts:
        dist = {(start, 0): 0}
        parent: dict = {}
        done: set = set()
        heap = [(0, start, 0)]
        while heap:
            d, u, b = heapq.heappop(heap)
            if (u, b) in done:
                continue
            if best is not None and d >= best[0]:
                break
            why = ctl.stop()
            if why:
                raise _Stop(why)
            done.add((u, b))
            if (u, b) == (start, 1):
                walk, x = 0, (start, 1)
                while x != (start, 0):
                    pu, pb, ei = parent[x]
                    walk ^= 1 << ei
                    x = (pu, pb)
                best = (d, walk)
                break
            for ei, v in adjacent[u]:
                state, nd = (v, b ^ (ei in s)), d + weight(ei)
                if state not in done and (state not in dist or nd < dist[state]):
                    dist[state] = nd
                    parent[state] = (u, b, ei)
                    heapq.heappush(heap, (nd, v, state[1]))
    d, walk = best
    for cycle in _simple_cycles(walk, src, dst):
        if len(s.intersection(_members(cycle))) & 1:
            return cycle, sum(weight(ei) for ei in _members(cycle))
    raise AssertionError("an odd even-subgraph holds an odd cycle")


def _members(bits: int) -> list[int]:
    out, ei = [], 0
    while bits:
        if bits & 1:
            out.append(ei)
        bits >>= 1
        ei += 1
    return out


def _simple_cycles(bits: int, src: Any, dst: Any) -> list[int]:
    """An even subgraph (every node of even degree, a loop counting two) split into edge-disjoint simple cycles, in a
    fixed order: from the smallest edge left, walk along the smallest unused edge at each node (one is always there:
    the degree is even) until a node repeats, and cut that cycle out."""
    left = set(_members(bits))
    incident: dict[int, list[int]] = {}
    for ei in sorted(left):
        incident.setdefault(src[ei], []).append(ei)
        if dst[ei] != src[ei]:
            incident.setdefault(dst[ei], []).append(ei)
    cycles = []
    while left:
        ei = min(left)
        at = src[ei]
        path, seen = [], {at: 0}
        while True:
            path.append(ei)
            at = dst[ei] if src[ei] == at else src[ei]
            if at in seen:
                loop = path[seen[at]:]
                cycles.append(sum(1 << e for e in loop))
                left.difference_update(loop)
                break
            seen[at] = len(path)
            ei = next(e for e in incident[at] if e in left and e != path[-1] and e not in path)
    return cycles


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    from .engine import _number
    if st.rank is None:
        return {"rank": None, "cycles": [], "count": 0, "weight": None, "certificate": None}, []
    ids, eids, src, dst = p._node_ids, p._edge_ids, p._edge_src, p._edge_dst

    def number(field: str, x: Any) -> dict:
        value, exact = _number(plan.exact(x), plan.rational)
        return {field: value, **({f"{field}_exact": exact} if plan.rational else {})}

    def ring(bits: int, w: Any) -> dict:
        """A simple cycle: its edges by ID, and its nodes in order around it, from its smallest node ID toward the
        neighbour with the smaller ID (a loop: one node; two parallel links: two)."""
        members = _members(bits)
        around: dict[int, list[tuple[int, int]]] = {}
        for ei in members:
            around.setdefault(src[ei], []).append((dst[ei], ei))
            if src[ei] != dst[ei]:
                around.setdefault(dst[ei], []).append((src[ei], ei))
        start = min(around)
        order, prev_edge, at = [start], None, start
        while True:
            nxt = min((v, ei) for v, ei in around[at] if ei != prev_edge)
            if nxt[0] == start:
                break
            order.append(nxt[0])
            at, prev_edge = nxt[0], nxt[1]
            if len(order) > len(members):
                break
        return {"nodes": [ids[v] for v in order], "edges": [eids[ei] for ei in members], "length": len(members),
                **number("weight", w)}

    cycles = [ring(bits, w) for bits, w in st.cycles]
    complete = st.interruption is None
    cert = None
    if plan.certificate:
        cert = {"kind": "witnesses", "witnesses": [[eids[ei] for ei in s] for s in st.witnesses]}
    total = sum(w for _, w in st.cycles) if complete else None
    return ({"rank": st.rank, "cycles": cycles, "count": len(cycles),
             **(number("weight", total) if complete else {"weight": None}), "certificate": cert},
            [CYCLES_NOTE] + ([CERTIFIED_CYCLES_NOTE] if cert else []))


CYCLES_NOTE = ("Each listed cycle is a simple cycle of links in scope; together they are independent. A minimum "
               "cycle basis is not unique: this one is de Pina's, from a spanning forest by edge ID.")
CERTIFIED_CYCLES_NOTE = ("The witnesses prove the basis minimum (de Pina's theorem): each cycle meets its witness "
                         "oddly and every earlier cycle's witness evenly, and is a lightest cycle meeting it oddly, "
                         "which verify_result recomputes.")
