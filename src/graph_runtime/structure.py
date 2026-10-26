"""Structure: how a graph falls apart into pieces, and where a single failure would split it.

``components`` partitions the nodes. Weakly: two nodes are together when hops in scope, each followed either way,
join them (the pieces a structure or a corpus falls into). Strongly: when a walk of hops leads from each to the other
(feedback loops, circular derivations, the communicating classes of a Markov chain). A strong component is
``initial`` when no hop enters it from outside, ``terminal`` when none leaves it (a terminal class of a reaction
network or a Markov chain: what a walk can never leave), and ``cyclic`` when a walk inside it returns to its start.
Strong components come from Tarjan's depth-first search (1972), weak ones from breadth-first search; both are linear.

``biconnectivity`` reads every edge in scope as a link between its two ends, whichever way it points, and finds the
articulation points (a node whose removal splits its component; ``splits`` says into how many pieces), the bridges
(an edge whose removal does) and, on request, the blocks (maximal pieces with no articulation point: every two of
their edges lie on a common cycle). One depth-first search with low points finds them all (Hopcroft and Tarjan,
1973). Parallel edges are distinct links, so two of them between the same nodes are never bridges; an edge from a
node to itself joins nothing and is in no block.

Both prove their answers on request, so that a checker needs no second search (:mod:`graph_runtime.verify`):

* components: ``groups``, every component the search finished, in topological order for strong components (no hop
  leads from a group to an earlier one) and closed (no hop leads out of the groups), each with a spanning tree from
  its first node (strong: one out of it and one into it). Trees prove a group connected; closure and order prove
  that nothing outside it belongs with it: each group is a component, listed or not, and so ``total`` is proven too.
* biconnectivity: the depth-first forest (each node's preorder number and the edge to its parent). A checker
  recomputes low points from the forest and the records, locally, and from them every claim.

An interrupted search lists only what it finished: a component once the search has left it, an articulation point,
a bridge or a block once the depth-first search has left the subtree that decides it. Each is exact as listed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

_DIRECTION = ("forward", "reverse")


def _either(plan: SimpleNamespace) -> list[bool]:
    """The relations in scope in either direction: what an undirected reading of the scope follows."""
    return [f or r for f, r in zip(plan.fwd, plan.rev)]


def _neighbors(p: Any, sides: tuple, u: int) -> list[tuple[int, int, int]]:
    """(edge, the node it leads to, 0 forward or 1 reverse) for every hop in ``sides`` from ``u``, in canonical
    order, filling the compiled lists as it reads them."""
    rel = p._edge_rel
    out = []
    for lists, base, marks, ends, flag in sides:
        hs = lists[u]
        if hs is None:
            hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
        out.extend((ei, ends[ei], flag) for ei in hs)
    return out


# ---------------------------------------------------------------------------------------------- components
def search_components(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """Every component of the selection (all nodes, or the components holding ``plan.nodes``), found in turn. A
    node is one expansion. ``st.groups`` holds the finished components as sorted node lists, in the order the search
    finished them; ``st.listed`` how many of them the answer lists (at least ``min_size`` nodes, holding a requested
    node when nodes are named); the search stops when one more would exceed ``max_results``."""
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, groups=[], listed=0)
    want = None if plan.nodes is None else set(plan.nodes)

    def keep(group: list[int]) -> bool:
        """Count a finished component; False when listing it would exceed the output limit."""
        if len(group) >= plan.min_size and (want is None or not want.isdisjoint(group)):
            if st.listed >= plan.max_results:
                st.interruption = "output_limit"
                return False
            st.listed += 1
        st.groups.append(group)
        return True

    (_strong if plan.connectivity == "strong" else _weak)(p, plan, ctl, st, keep)
    return st


def _weak(p: Any, plan: SimpleNamespace, ctl: Any, st: SimpleNamespace, keep: Any) -> None:
    from .engine import _sides
    either = _either(plan)
    sides, rel = _sides(p, either, either), p._edge_rel
    seen = bytearray(len(p._node_ids))
    examined = 0
    for r in (range(len(p._node_ids)) if plan.nodes is None else plan.nodes):
        if seen[r]:
            continue
        seen[r] = 1
        group, i = [r], 0
        while i < len(group):
            u = group[i]
            i += 1
            why = ctl.stop()
            if why:
                st.interruption = why
                break
            for lists, base, marks, ends, _ in sides:
                hs = lists[u]
                if hs is None:
                    hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
                examined += len(hs)
                for ei in hs:
                    v = ends[ei]
                    if not seen[v]:
                        seen[v] = 1
                        group.append(v)
        st.discovered += len(group)
        if st.interruption or not keep(sorted(group)):
            break
    st.examined = examined


def _strong(p: Any, plan: SimpleNamespace, ctl: Any, st: SimpleNamespace, keep: Any) -> None:
    """Tarjan's algorithm, iterative: a component is finished when the search returns to its first node with no
    deeper node left on the stack below it; components finish sinks first."""
    from .engine import _sides
    sides = _sides(p, plan.fwd, plan.rev)
    n = len(p._node_ids)
    index, low, on = [-1] * n, [0] * n, bytearray(n)
    stack: list[int] = []
    counter = examined = 0

    def enter(u: int) -> list:
        nonlocal counter, examined
        index[u] = low[u] = counter
        counter += 1
        stack.append(u)
        on[u] = 1
        hs = [v for _, v, _ in _neighbors(p, sides, u)]
        examined += len(hs)
        return [u, hs, 0]

    for r in (range(n) if plan.nodes is None else plan.nodes):
        if index[r] >= 0:
            continue
        why = ctl.stop()
        if why:
            st.interruption = why
            break
        call = [enter(r)]
        while call:
            frame = call[-1]
            u, hs, i = frame
            if i < len(hs):
                frame[2] = i + 1
                v = hs[i]
                if index[v] < 0:
                    why = ctl.stop()
                    if why:
                        st.interruption = why
                        break
                    call.append(enter(v))
                elif on[v] and index[v] < low[u]:
                    low[u] = index[v]
                continue
            call.pop()
            if call and low[u] < low[call[-1][0]]:
                low[call[-1][0]] = low[u]
            if low[u] == index[u]:
                group = []
                while True:
                    x = stack.pop()
                    on[x] = 0
                    group.append(x)
                    if x == u:
                        break
                if not keep(sorted(group)):
                    break
        if st.interruption:
            break
    st.examined, st.discovered = examined, counter


def answer_components(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    """The listed components in ascending order of their first node, each with its size and the number of edges in
    scope between its nodes (strong ones with their flags); ``total``: how many components the selection has
    (whatever their size), when the search finished."""
    from .engine import _back_sides, _sides
    ids = p._node_ids
    strong = plan.connectivity == "strong"
    want = None if plan.nodes is None else set(plan.nodes)
    either = _either(plan)
    sides = _sides(p, plan.fwd, plan.rev) if strong else _sides(p, either, either)
    back = _back_sides(p, plan.fwd, plan.rev) if strong else None
    listed = sorted((g for g in st.groups
                     if len(g) >= plan.min_size and (want is None or not want.isdisjoint(g))), key=lambda g: g[0])
    entries = []
    for g in listed:
        member = set(g)
        inside, leaves, loop = set(), False, False
        for u in g:
            for ei, v, _ in _neighbors(p, sides, u):
                if v in member:
                    inside.add(ei)
                    loop = loop or v == u
                else:
                    leaves = True
        entry: dict = {"nodes": [ids[u] for u in g], "size": len(g), "edges": len(inside)}
        if strong:
            enters = any(v not in member for u in g for _, v, _ in _neighbors(p, back, u))
            entry.update(cyclic=len(g) > 1 or loop, initial=not enters, terminal=not leaves)
        entries.append(entry)
    done = st.interruption is None
    total = None
    if done:
        total = len(st.groups) if want is None else sum(1 for g in st.groups if not want.isdisjoint(g))
    answer = {"components": entries, "count": len(entries), "total": total, "certificate": None}
    if plan.certificate:
        groups = []
        for g in (reversed(st.groups) if strong else st.groups):    # Tarjan finishes sinks first
            member = set(g) if len(g) > 1 else None
            groups.append({"nodes": [ids[u] for u in g], "out": _tree(p, sides, g, member, "from"),
                           "in": _tree(p, back, g, member, "to") if strong else None})
        answer["certificate"] = {"kind": "partition", "groups": groups}
    return answer, []


def _tree(p: Any, sides: tuple, group: list[int], member: set | None, end: str) -> list[dict]:
    """A breadth-first spanning tree of ``group`` from its first node over hops between its members: each other
    member with the hop that reaches it (``end`` "from": the node it comes from), or, over the hops read backwards,
    the hop that leads from it toward the root (``end`` "to": where it goes). A group of one node has no hop."""
    if member is None:
        return []
    ids, eids, names, rel = p._node_ids, p._edge_ids, p._relations, p._edge_rel
    root = group[0]
    parent = {root: None}
    queue, out = [root], []
    for u in queue:
        for ei, v, flag in _neighbors(p, sides, u):
            if v in member and v not in parent:
                parent[v] = (u, ei, flag)
                queue.append(v)
    for v in group[1:]:
        u, ei, flag = parent[v]
        out.append({"node": ids[v], "via": {"edge": eids[ei], "relation": names[rel[ei]],
                                            "direction": _DIRECTION[flag], end: ids[u]}})
    return out


# ---------------------------------------------------------------------------------------------- biconnectivity
def search_biconnectivity(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """One iterative depth-first search over the links (every edge in scope, either way; a link from a node to
    itself skipped) from each unvisited node of the selection in ascending order. ``low[u]``: the least preorder
    number a link from u's subtree reaches, other than u's own link to its parent (a parallel link to the parent
    counts). When a child c of q finishes with ``low[c] >= pre[q]``, q separates c's subtree from the rest (its
    links form a block with the link to c); with ``low[c] > pre[q]`` that link is a bridge. A node is listed as an
    articulation point when it finishes: a root with two children or more, another node with a separated child."""
    from .engine import _sides
    either = _either(plan)
    sides = _sides(p, either, either)
    n = len(p._node_ids)
    pre, low, via, parent = [-1] * n, [0] * n, [-1] * n, [-1] * n
    separated, kids = [0] * n, [0] * n
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, points={}, bridges=[], blocks=[], pre=pre,
                         via=via, roots=0)
    links: list[int] = []                        # links not yet in a block, as the search met them
    counter = examined = 0

    def enter(u: int, ei: int, q: int) -> list:
        nonlocal counter, examined
        pre[u] = low[u] = counter
        counter += 1
        via[u], parent[u] = ei, q
        hs = [(e, v) for e, v, _ in _neighbors(p, sides, u)]
        examined += len(hs)
        return [u, hs, 0]

    for r in (range(n) if plan.nodes is None else plan.nodes):
        if pre[r] >= 0:
            continue
        why = ctl.stop()
        if why:
            st.interruption = why
            break
        st.roots += 1
        call = [enter(r, -1, -1)]
        while call:
            frame = call[-1]
            u, hs, i = frame
            if i < len(hs):
                frame[2] = i + 1
                ei, w = hs[i]
                if w == u or ei == via[u]:
                    continue
                if pre[w] < 0:
                    why = ctl.stop()
                    if why:
                        st.interruption = why
                        break
                    kids[u] += 1
                    if plan.blocks:
                        links.append(ei)
                    call.append(enter(w, ei, u))
                elif pre[w] < pre[u]:                   # a link back to an ancestor (from the deeper end only)
                    if pre[w] < low[u]:
                        low[u] = pre[w]
                    if plan.blocks:
                        links.append(ei)
                continue
            call.pop()
            q = parent[u]
            if q >= 0:
                if low[u] < low[q]:
                    low[q] = low[u]
                if low[u] >= pre[q]:
                    separated[q] += 1
                    if plan.blocks:
                        block = []
                        while True:
                            e = links.pop()
                            block.append(e)
                            if e == via[u]:
                                break
                        st.blocks.append(block)
                if low[u] > pre[q]:
                    st.bridges.append(via[u])
                if separated[u]:
                    st.points[u] = separated[u] + 1
            elif kids[u] > 1:
                st.points[u] = kids[u]
        if st.interruption:
            break
    st.examined, st.discovered = examined, counter
    return st


def answer_biconnectivity(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    ids, eids, names, rel, src, dst = (p._node_ids, p._edge_ids, p._relations, p._edge_rel, p._edge_src,
                                       p._edge_dst)
    points = [{"node": ids[u], "splits": st.points[u]} for u in sorted(st.points)]
    bridges = [{"edge": eids[ei], "relation": names[rel[ei]], "source": ids[src[ei]], "target": ids[dst[ei]]}
               for ei in sorted(st.bridges)]
    blocks = None
    if plan.blocks:
        blocks = []
        for block in sorted((sorted(b) for b in st.blocks), key=lambda b: b[0]):
            ends = sorted({src[ei] for ei in block} | {dst[ei] for ei in block})
            blocks.append({"nodes": [ids[u] for u in ends], "edges": [eids[ei] for ei in block]})
    done = st.interruption is None
    answer = {"articulation_points": points, "bridges": bridges, "blocks": blocks,
              "components": st.roots if done else None, "certificate": None}
    if plan.certificate:
        pre, via = st.pre, st.via
        answer["certificate"] = {"kind": "dfs", "forest": [
            {"node": ids[u], "pre": pre[u], "via": eids[via[u]] if via[u] >= 0 else None}
            for u in range(len(ids)) if pre[u] >= 0]}
    return answer, []
