"""Dominators: the nodes every walk from the sources to a node must pass through.

A node d dominates v when every walk of hops in scope from a source to v passes through d. The nearest such node is
v's immediate dominator, and they form a tree (Prosser, 1959; Lowry and Medlock, 1969): the dominators of v are its
ancestors. A dominator is an unavoidable step: the intermediate every route from a feedstock to a product forms, the
evidence every derivation of a claim rests on, the stage every path through a workflow takes, a single point of
failure of a network. With several sources, a start joined to each of them stands for "some source"; a node no
single node dominates has no immediate dominator.

The tree comes from Lengauer and Tarjan's algorithm (1979): a depth-first search, semi-dominators by path
compression, and dominators from them. The proof comes from Georgiadis and Tarjan ("Dominator tree certification
and divergent spanning trees", 2016): a preorder of the tree is *low-high* when every node v other than the start
has an arc from its parent, or two arcs (u, v) and (w, v) with u before v before w and w not a descendant of v. A
tree with the parent property (every arc (u, v) leaves a descendant of v's parent) and a low-high order is the
dominator tree, and both are checked arc by arc in linear time, without computing dominators again. To build the
order, the semi-dominator computation also gives two independent spanning trees (their Algorithm 8: each node takes
its depth-first parent in one tree and the last arc of a high path from its semi-dominator in the other, by a colour
rule), and the order is assembled from them by deleting nodes one by one and inserting them back beside their parent
in one of the trees (their Algorithm 7), the insertions run off-line on integer intervals (their Algorithm 5).
"""

from __future__ import annotations

from bisect import bisect_right
from types import SimpleNamespace
from typing import Any


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """The region the sources reach, by an iterative depth-first search from a start (number 0) joined to each source
    in ascending order, its hops in canonical order (each node reached is one expansion), then its dominator tree and,
    on request, a low-high order of it. An interrupted search claims nothing: a dominator is a claim about every
    walk."""
    from .engine import _sides
    from .structure import _neighbors
    sides = _sides(p, plan.fwd, plan.rev)
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, nodes=None, idom=None, order=None)
    number: dict[int, int] = {}
    nodes = [-1]                                  # nodes[k]: the projection node numbered k (0: the start)
    parent = [0]
    arcs: list[tuple[int, int]] = []              # (from, to) by projection node; -1 is the start
    call: list[list] = []

    def enter(u: int, f: int) -> bool:
        why = ctl.stop()
        if why:
            st.interruption = why
            return False
        number[u] = len(nodes)
        nodes.append(u)
        parent.append(f)
        hs = [v for _, v, _ in _neighbors(p, sides, u) if v != u]
        st.examined += len(hs)
        call.append([u, hs, 0])
        return True

    for s in plan.sources:
        arcs.append((-1, s))
        if s in number:
            continue
        if not enter(s, 0):
            break
        while call:
            frame = call[-1]
            u, hs, i = frame
            if i == len(hs):
                call.pop()
                continue
            frame[2] = i + 1
            v = hs[i]
            arcs.append((u, v))
            if v not in number and not enter(v, number[u]):
                break
        if st.interruption:
            break
    st.discovered = len(nodes) - 1
    if st.interruption:
        return st
    n = len(nodes)
    pred: list[list[int]] = [[] for _ in range(n)]
    for u, v in arcs:
        pred[number[v]].append(0 if u < 0 else number[u])
    idom, semi, rd, last = _lengauer_tarjan(n, parent, pred)
    st.nodes, st.number, st.idom = nodes, number, idom
    if plan.certificate:
        st.order = _low_high(n, parent, pred, idom, semi, rd, last)
    return st


def _lengauer_tarjan(n: int, parent: list[int], pred: list[list[int]]) -> tuple[list, list, list, list]:
    """Immediate dominators of vertices numbered 0..n-1 in depth-first preorder (0 the start), with ``semi`` (the
    semi-dominators), ``rd`` (relative dominators: a vertex of least semi-dominator on the tree path just below
    sd(v) to v) and ``last`` (the tail of an arc that ends a high path from sd(v) to v), by the simple version of
    Lengauer and Tarjan's algorithm (path compression)."""
    semi, label, anc = list(range(n)), list(range(n)), [-1] * n
    idom, rd, last = [0] * n, list(range(n)), [-1] * n
    bucket: list[list[int]] = [[] for _ in range(n)]

    def evaluate(v: int) -> int:
        if anc[v] < 0:
            return v
        path = []
        x = v
        while anc[anc[x]] >= 0:
            path.append(x)
            x = anc[x]
        while path:                                # compress, from the top of the path down
            x = path.pop()
            a = anc[x]
            if semi[label[a]] < semi[label[x]]:
                label[x] = label[a]
            anc[x] = anc[a]
        return label[v]

    for w in range(n - 1, 0, -1):
        for v in pred[w]:
            u = evaluate(v)
            if semi[u] < semi[w]:
                semi[w], last[w] = semi[u], v
        bucket[semi[w]].append(w)
        f = parent[w]
        anc[w] = f
        for v in bucket[f]:
            u = evaluate(v)
            rd[v] = u
            idom[v] = u if semi[u] < semi[v] else f
        bucket[f] = []
    for w in range(1, n):
        if idom[w] != semi[w]:
            idom[w] = idom[idom[w]]
    return idom, semi, rd, last


def _low_high(n: int, parent: list[int], pred: list[list[int]], idom: list[int], semi: list[int], rd: list[int],
              last: list[int]) -> list[int]:
    """A low-high order of the dominator tree (vertex numbers, the start first), by Georgiadis and Tarjan's
    Algorithms 8, 7 and 5."""
    kids: list[list[int]] = [[] for _ in range(n)]
    for v in range(1, n):
        kids[idom[v]].append(v)
    dpre, size = [0] * n, [1] * n                  # a preorder of the tree, for ancestor tests and derived arcs
    walk, seen = [0], 0
    order = []
    while walk:
        x = walk.pop()
        dpre[x] = seen
        seen += 1
        order.append(x)
        walk.extend(reversed(kids[x]))
    for x in reversed(order):
        if x:
            size[idom[x]] += size[x]
    starts = [[dpre[c] for c in kids[x]] for x in range(n)]

    def derived(x: int, v: int) -> int:
        """The derived arc's tail: v's parent itself, or the child of v's parent that holds x."""
        d = idom[v]
        if x == d:
            return d
        return kids[d][bisect_right(starts[d], dpre[x]) - 1]

    arc_set = {(u, v) for v in range(1, n) for u in pred[v]}
    red = [False] * n
    b, r = [0] * n, [0] * n
    for v in range(1, n):                          # Algorithm 8: two independent spanning trees
        if semi[v] == semi[rd[v]] or red[rd[v]]:
            b[v], r[v] = last[v], parent[v]
        else:
            red[v] = True
            b[v], r[v] = parent[v], last[v]
    for v in range(1, n):                          # Algorithm 7, steps 1 and 2: the derived trees
        if (idom[v], v) in arc_set:
            b[v] = r[v] = idom[v]
        else:
            b[v], r[v] = derived(b[v], v), derived(r[v], v)
    child_b: list[set[int]] = [set() for _ in range(n)]
    child_r: list[set[int]] = [set() for _ in range(n)]
    for v in range(1, n):
        child_b[b[v]].add(v)
        child_r[r[v]].add(v)

    def ready(v: int) -> bool:
        return (1 if b[v] == r[v] else 2) > len(child_b[v]) + len(child_r[v])
    alive = [True] * n
    stack = [v for v in range(n - 1, 0, -1) if ready(v)]
    deleted: list[tuple[int, int, int]] = []
    while stack:                                   # Algorithm 7, step 4: delete, recording where to insert back
        v = stack.pop()
        if not alive[v] or not ready(v):
            continue
        alive[v] = False
        bv, rv = b[v], r[v]
        child_b[bv].discard(v)
        child_r[rv].discard(v)
        moved = None
        if child_r[v]:                             # a leaf in B: its child in R hangs from v's parent in R, and v
            moved = child_r[v].pop()               # goes back beside that parent (the roles of B and R swap)
            r[moved] = rv
            child_r[rv].add(moved)
            deleted.append((v, rv, bv))
        else:                                      # a leaf in R: the same in B
            if child_b[v]:
                moved = child_b[v].pop()
                b[moved] = bv
                child_b[bv].add(moved)
            deleted.append((v, bv, rv))
        for x in (bv, rv, moved):
            if x and alive[x]:
                stack.append(x)
    if len(deleted) != n - 1:
        raise RuntimeError("no vertex can be deleted: the spanning trees are not independent")
    return _insertions(n, idom, kids, deleted)


def _insertions(n: int, idom: list[int], kids: list[list[int]], deleted: list[tuple[int, int, int]]) -> list[int]:
    """Algorithm 7's insertions, in reverse order of deletion, run off-line (Algorithm 5). ``deleted`` holds (v, its
    parent in the tree it was a leaf of, its parent in the other): v goes into its dominator tree parent's list of
    children anywhere (when that first parent is its dominator), or else just before the first parent if the other
    is before it, just after otherwise. Every list gets integer intervals sized by an instruction tree,
    so that an insertion and an order test are O(1); the final intervals number each list in order. Returns the
    preorder of the tree with each vertex's children in their list order: a low-high order."""
    steps = list(reversed(deleted))
    anchor = {}                                    # vertex -> the vertex it is inserted beside (None: the list)
    for v, bv, rv in steps:
        anchor[v] = None if bv == idom[v] else bv
    size = [1] * n
    for v, _, _ in deleted:                        # deletion order is a reverse of insertion: children first
        a = anchor[v]
        if a is not None:
            size[a] += size[v]
    span: dict[int, list[int]] = {}               # vertex -> [i, j]; a list root -> [i, j] under key -1 - d
    for d in range(n):
        if kids[d]:
            span[-1 - d] = [0, sum(size[v] for v in kids[d] if anchor[v] is None)]
    for v, bv, rv in steps:
        if anchor[v] is None:
            i, j = span[-1 - idom[v]]
            span[-1 - idom[v]] = [i, j - size[v]]
            span[v] = [j - size[v] + 1, j]
        else:
            i, j = span[bv]
            if span[rv][0] > span[bv][1]:          # R-parent after B-parent: insert just after it
                span[bv] = [i, j - size[v]]
                span[v] = [j - size[v] + 1, j]
            else:
                span[v] = [i, i + size[v] - 1]
                span[bv] = [i + size[v], j]
    order, walk = [], [0]
    while walk:
        x = walk.pop()
        order.append(x)
        walk.extend(sorted(kids[x], key=lambda c: span[c][0], reverse=True))
    return order


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    """With no targets, every reached node and its immediate dominator (null when no single node dominates it), by
    node ID; with targets, each target's dominators, from the sources toward it. On request, the certificate: every
    reached node with its immediate dominator, in a low-high order."""
    ids = p._node_ids
    if st.interruption:
        return {"reached": None, "tree": None, "chains": None, "certificate": None}, []
    nodes, number, idom = st.nodes, st.number, st.idom

    def dom(k: int) -> Any:
        return ids[nodes[idom[k]]] if idom[k] else None
    out: dict = {"reached": len(nodes) - 1, "tree": None, "chains": None, "certificate": None}
    if plan.targets is None:
        out["tree"] = [{"node": ids[u], "idom": dom(number[u])} for u in sorted(number)]
    else:
        chains = []
        for t in plan.targets:
            k = number.get(t)
            above = []
            while k:
                k = idom[k]
                if k:
                    above.append(ids[nodes[k]])
            chains.append({"target": ids[t], "reachable": t in number, "dominators": above[::-1]})
        out["chains"] = chains
    if plan.certificate:
        out["certificate"] = {"kind": "low_high", "order": [{"node": ids[nodes[k]], "idom": dom(k)}
                                                            for k in st.order if k]}
    return out, []
