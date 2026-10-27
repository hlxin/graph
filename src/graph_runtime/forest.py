"""Minimum spanning forests: the lightest set of links that keeps every connected piece of a graph connected.

Read undirected (each edge in scope a link between its ends, whichever way the scope follows it, as biconnectivity
reads it), the edges in scope join the projection's nodes into connected pieces. A spanning forest holds one tree
per piece, joining all of its nodes with the piece's node count less one links; a minimum one has the least total
weight, a maximum one (``objective: max``) the greatest. The backbone of a similarity or interaction network, the
single-linkage clustering of its nodes (cut the heaviest links), the cheapest way to connect sites.

Kruskal's algorithm (1956): the edges in order of (weight, edge ID), or of weight descending then edge ID for a
maximum, each kept when it joins two trees (a union-find with path halving and union by size). Weights are compared
exactly, so the forest is the one that is least in that strict order: unique, and minimum (or maximum) by weight. Any
finite weights work, negative ones too; floats are exact rationals in the total. A loop never joins two trees.

The answer proves itself, with no certificate (Tarjan's cycle property): a set of edges in scope with no cycle, whose
trees join the ends of every other edge in scope, and in which every edge left out comes after every edge of the tree
path between its ends in that order, is the forest Kruskal's algorithm keeps. A checker finds the path maxima by
binary lifting, in time O((n + m) log n), from the records alone.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """Each node's edges in scope read once (one expansion: the edges it is the source of, so each edge once), then
    Kruskal's algorithm over them. An interrupted search claims nothing."""
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, forest=None)
    rel, dst, fwd, rev = p._edge_rel, p._edge_dst, plan.fwd, plan.rev
    links = []
    for u in range(len(p._node_ids)):
        why = ctl.stop()
        if why:
            st.interruption = why
            return st
        out = p._out[u]
        st.examined += len(out)
        links.extend(ei for ei in out if (fwd[rel[ei]] or rev[rel[ei]]) and dst[ei] != u)
    weight = plan.weight
    sign = -1 if plan.maximum else 1
    links.sort(key=lambda ei: (sign * weight(ei), ei))       # edge indices ascend with edge IDs
    up = list(range(len(p._node_ids)))
    size = [1] * len(up)
    src = p._edge_src

    def root(x: int) -> int:
        while up[x] != x:
            up[x] = up[up[x]]                                # path halving
            x = up[x]
        return x

    forest = []
    for ei in links:
        a, b = root(src[ei]), root(dst[ei])
        if a == b:
            continue
        if size[a] < size[b]:
            a, b = b, a
        up[b] = a
        size[a] += size[b]
        forest.append(ei)
    st.forest = sorted(forest)
    st.discovered = len(p._node_ids)
    return st


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    from .engine import _number
    if st.forest is None:
        return {"edges": None, "count": None, "trees": None, "weight": None}, []
    total = sum(plan.weight(ei) for ei in st.forest)
    value, exact = _number(plan.exact(total), plan.rational)
    return ({"edges": [p._edge_ids[ei] for ei in st.forest], "count": len(st.forest),
             "trees": len(p._node_ids) - len(st.forest), "weight": value,
             **({"weight_exact": exact} if plan.rational else {})}, [FOREST_NOTE])


FOREST_NOTE = ("The forest proves itself against the records (the cycle property): every edge in scope left out joins "
               "two nodes of one tree and comes after every edge of the tree path between them in the order of "
               "(weight, edge ID), so no other forest is lighter, and ties are broken by edge ID.")
