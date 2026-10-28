"""Canonical forms and isomorphism of induced subgraphs: deduplicate molecules, motifs and provenance fragments.

The graph of a node set is its induced subgraph: the set's nodes (colored by their declared types, or all alike) and
the edges in scope with both ends in the set, each with its own relation, directed (from its source to its target) or
as an undirected link. Parallel edges and loops count with their multiplicity.

Color refinement (one-dimensional Weisfeiler-Leman): a node's new color is its old color with the sorted multiset of
(neighbour's color, relation) over its out-edges and, separately, over its in-edges (directed; a loop in both), or over
its links (undirected; a loop once); colors are ranks of these signatures in sorted order, so they depend on structure
alone, never on IDs. Refinement repeats until no class splits.

The canonical form comes from individualization and refinement (McKay and Piperno, 2014): refine; if a class has
several nodes, take the smallest such class (the lowest color on ties) and, for each of its nodes in turn, give that
node a color of its own just before the rest of its class and search on; every discrete partition (a leaf) orders the
nodes, and the order whose encoding is least is canonical. The encoding is the nodes' colors in that order and the
sorted edges ``[i, j, relation]`` between positions. Two leaves with the same encoding give an automorphism; a child
whose node lies in the orbit of one already searched, under the automorphisms found that fix the current prefix, has
the same leaves and is skipped. The search is exact: the least encoding is the same for every isomorphic copy, and
only which of the leaves achieving it is reported depends on node IDs. Worst cases stay exponential (the problem is
not known to be polynomial), and the expansion budget bounds the search.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ._canonical import digest


class _Stop(Exception):
    """A limit, a deadline or a cancellation stopped the search."""


def _charge(ctl: Any, k: int) -> None:
    why = ctl.take(k)
    if why:
        raise _Stop(why)


def induced(p: Any, nodes: list[int], plan: SimpleNamespace, ctl: Any, st: SimpleNamespace) -> SimpleNamespace:
    """The graph of a node set: its nodes in ascending ID (local indices), their colors, and its edges in scope with
    both ends in the set, as (local source, local target, relation, edge index), by edge ID. Each node read is one
    expansion, and so is each edge leaving it and each type it has, charged before they are read."""
    local = {u: k for k, u in enumerate(nodes)}
    marks = [a or b for a, b in zip(plan.fwd, plan.rev)]
    edges, colors = [], []
    names, masks = p._node_types, p._node_mask                 # a node's types, with their ancestors
    for u in nodes:
        out = p._out[u]
        mask = masks[u] if plan.node_colors == "types" else 0
        _charge(ctl, 1 + len(out) + bin(mask).count("1"))
        st.examined += len(out)
        st.discovered += 1
        for ei in out:
            v = p._edge_dst[ei]
            if v in local and marks[p._edge_rel[ei]]:
                edges.append((local[u], local[v], p._relations[p._edge_rel[ei]], ei))
        have = []
        while mask:
            low = mask & -mask
            have.append(names[low.bit_length() - 1])
            mask ^= low
        colors.append(tuple(have))
    edges.sort(key=lambda e: p._edge_ids[e[3]])
    return SimpleNamespace(nodes=nodes, n=len(nodes), edges=edges, colors=colors, directed=plan.directed)


def _ranks(keys: list) -> list[int]:
    order = {k: r for r, k in enumerate(sorted(set(keys)))}
    return [order[k] for k in keys]


def _neighbours(g: SimpleNamespace) -> tuple[list, list]:
    out: list[list] = [[] for _ in range(g.n)]
    inn: list[list] = [[] for _ in range(g.n)]
    for a, b, rel, _ in g.edges:
        if g.directed:
            out[a].append((b, rel))
            inn[b].append((a, rel))
        else:
            out[a].append((b, rel))
            if a != b:
                out[b].append((a, rel))
    return out, inn


def refine(colors: list[int], out: list, inn: list, directed: bool, ctl: Any, arcs: int) -> list[int]:
    """Refine to stability: each round one expansion per node and per arc read."""
    while True:
        _charge(ctl, len(colors) + arcs)
        if directed:
            sigs = [(colors[v], tuple(sorted((colors[w], r) for w, r in out[v])),
                     tuple(sorted((colors[w], r) for w, r in inn[v]))) for v in range(len(colors))]
        else:
            sigs = [(colors[v], tuple(sorted((colors[w], r) for w, r in out[v]))) for v in range(len(colors))]
        new = _ranks(sigs)
        if max(new, default=-1) == max(colors, default=-1):
            return new
        colors = new


def _encoding(g: SimpleNamespace, order: list[int]) -> tuple:
    pos = {v: i for i, v in enumerate(order)}
    edges = []
    for a, b, rel, _ in g.edges:
        i, j = pos[a], pos[b]
        if not g.directed and i > j:
            i, j = j, i
        edges.append((i, j, rel))
    return (tuple(g.colors[v] for v in order), tuple(sorted(edges)))


def canonical(g: SimpleNamespace, ctl: Any) -> list[int]:
    """The canonical order of a graph's nodes (local indices): the first leaf, in search order, with the least
    encoding."""
    out, inn = _neighbours(g)
    arcs = sum(len(x) for x in out) + sum(len(x) for x in inn)

    def cells(colors: list[int]) -> list[list[int]]:
        by: dict[int, list[int]] = {}
        for v, c in enumerate(colors):
            by.setdefault(c, []).append(v)
        return [by[c] for c in sorted(by)]

    def target(colors: list[int]) -> list[int] | None:
        multi = [c for c in cells(colors) if len(c) > 1]
        return min(multi, key=lambda c: (len(c), colors[c[0]])) if multi else None

    def individualized(colors: list[int], v: int) -> list[int]:
        _charge(ctl, len(colors))
        c = colors[v]
        return _ranks([(x, 0 if u == v else 1) if x == c else (x, 0) for u, x in enumerate(colors)])

    best: list = [None, None]                                  # the least encoding, and its order
    gens: list[list[int]] = []                                 # automorphisms found

    def leaf(colors: list[int]) -> None:
        _charge(ctl, g.n + len(g.edges))
        order = sorted(range(g.n), key=colors.__getitem__)
        enc = _encoding(g, order)
        if best[0] is None or enc < best[0]:
            best[0], best[1] = enc, order
        elif enc == best[0]:
            perm = [0] * g.n
            for a, b in zip(best[1], order):
                perm[a] = b
            if any(perm[v] != v for v in range(g.n)):
                gens.append(perm)

    def orbit_of(prefix: list[int]) -> Any:
        usable = [h for h in gens if all(h[x] == x for x in prefix)]
        if not usable:
            return lambda v: v
        _charge(ctl, g.n * len(usable))
        parent = list(range(g.n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for h in usable:
            for x in range(g.n):
                a, b = find(x), find(h[x])
                if a != b:
                    parent[max(a, b)] = min(a, b)
        return find
    start = refine(_ranks(list(g.colors)), out, inn, g.directed, ctl, arcs)
    cell = target(start)
    if cell is None:
        leaf(start)
        return best[1]
    frames = [[start, [], cell, 0, []]]                        # colors, prefix, target cell, next child, searched
    while frames:
        frame = frames[-1]
        colors, prefix, cell, k, searched = frame
        if k == len(cell):
            frames.pop()
            continue
        frame[3] = k + 1
        v = cell[k]
        if searched:
            find = orbit_of(prefix)
            if find(v) in {find(x) for x in searched}:
                continue
        searched.append(v)
        child = refine(individualized(colors, v), out, inn, g.directed, ctl, arcs)
        nxt = target(child)
        if nxt is None:
            leaf(child)
        else:
            frames.append([child, prefix + [v], nxt, 0, []])
    return best[1]


def form(p: Any, g: SimpleNamespace, order: list[int]) -> dict:
    """The canonical encoding of a graph under an order, with its identity."""
    colors, edges = _encoding(g, order)
    encoding = {"nodes": [list(c) for c in colors], "edges": [list(e) for e in edges], "directed": g.directed}
    return {"order": [p._node_ids[g.nodes[v]] for v in order], "encoding": encoding,
            "canonical_id": digest({"kind": "graph_runtime.canonical_form", **encoding})}


def search_form(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, graph=None, order=None)
    try:
        g = st.graph = induced(p, plan.nodes, plan, ctl, st)
        st.order = canonical(g, ctl)
    except _Stop as stop:
        st.interruption = str(stop)
    return st


def answer_form(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    if st.order is None:
        return {"order": None, "encoding": None, "canonical_id": None}, []
    return form(p, st.graph, st.order), [FORM_NOTE]


def _joint(left: SimpleNamespace, right: SimpleNamespace, ctl: Any) -> dict | None:
    """A proof that two graphs are not isomorphic, when an easy one exists: other node or edge counts, or color
    refinement of their disjoint union giving the two sides other color histograms after some round."""
    if left.n != right.n or len(left.edges) != len(right.edges):
        return {"kind": "counts"}
    union = SimpleNamespace(n=left.n + right.n, directed=left.directed,
                            edges=left.edges + [(a + left.n, b + left.n, rel, ei) for a, b, rel, ei in right.edges])
    out, inn = _neighbours(union)
    arcs = sum(len(x) for x in out) + sum(len(x) for x in inn)
    colors = _ranks(list(left.colors) + list(right.colors))
    rounds = 0
    while True:
        if sorted(colors[:left.n]) != sorted(colors[left.n:]):
            return {"kind": "refinement", "rounds": rounds}
        _charge(ctl, union.n + arcs)
        if union.directed:
            sigs = [(colors[v], tuple(sorted((colors[w], r) for w, r in out[v])),
                     tuple(sorted((colors[w], r) for w, r in inn[v]))) for v in range(union.n)]
        else:
            sigs = [(colors[v], tuple(sorted((colors[w], r) for w, r in out[v]))) for v in range(union.n)]
        new = _ranks(sigs)
        if max(new, default=-1) == max(colors, default=-1):
            return None
        colors, rounds = new, rounds + 1


def search_iso(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, left=None, right=None, proof=None,
                         orders=None)
    try:
        st.left, st.right = induced(p, plan.left, plan, ctl, st), induced(p, plan.right, plan, ctl, st)
        st.proof = _joint(st.left, st.right, ctl)
        if st.proof is None:
            st.orders = (canonical(st.left, ctl), canonical(st.right, ctl))
    except _Stop as stop:
        st.interruption = str(stop)
    return st


def answer_iso(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    ids, eids = p._node_ids, p._edge_ids
    if st.interruption is not None:
        return {"isomorphic": None, "mapping": [], "edge_mapping": [], "certificate": None}, []
    if st.proof is not None:
        return {"isomorphic": False, "mapping": [], "edge_mapping": [],
                "certificate": st.proof if plan.certificate else None}, [ISO_NOTE]
    lo, ro = st.orders
    if form(p, st.left, lo)["canonical_id"] != form(p, st.right, ro)["canonical_id"]:
        return {"isomorphic": False, "mapping": [], "edge_mapping": [], "certificate": None}, [ISO_NOTE]
    mapping = sorted(({"left": ids[st.left.nodes[a]], "right": ids[st.right.nodes[b]]} for a, b in zip(lo, ro)),
                     key=lambda x: x["left"])

    def keyed(g: SimpleNamespace, order: list[int]) -> dict:
        pos = {v: i for i, v in enumerate(order)}
        by: dict = {}
        for a, b, rel, ei in g.edges:
            i, j = pos[a], pos[b]
            if not g.directed and i > j:
                i, j = j, i
            by.setdefault((i, j, rel), []).append(eids[ei])
        return by
    lk, rk = keyed(st.left, lo), keyed(st.right, ro)
    edge_mapping = sorted(({"left": a, "right": b} for key in lk for a, b in zip(sorted(lk[key]), sorted(rk[key]))),
                          key=lambda x: x["left"])
    return {"isomorphic": True, "mapping": mapping, "edge_mapping": edge_mapping, "certificate": None}, [ISO_NOTE]


FORM_NOTE = ("The encoding is the induced subgraph relabeled by the order, which a checker confirms; that it is the "
             "least over every order (so the same for every isomorphic copy) is the search's, not a certificate.")
ISO_NOTE = ("An isomorphism is proven by its node and edge bijections; a non-isomorphism by other counts or color "
            "refinement histograms when the certificate gives them, and otherwise by the exact canonical search.")
