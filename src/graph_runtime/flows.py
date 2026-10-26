"""Flows and matchings: how much can get through, what stops more from getting through, and who can be paired.

``max_flow`` sends as much as the capacities allow from the sources to the targets (Ford and Fulkerson, 1956). Each
hop in scope carries at most its edge's capacity (an edge in scope both ways carries flow either way, up to its
capacity each way, and its net flow is reported); with ``node_capacity``, every node other than the sources and
targets passes at most that much. Capacities default to 1, so the value is then the number of edge-disjoint paths,
and with ``node_capacity`` 1 the number of internally node-disjoint ones (Menger, 1927): how many independent lines
connect the sources to the targets. The answer gives the flow on each edge and a minimum cut: the hops (and nodes)
whose removal disconnects the targets, whose capacities add up to the value. By the max-flow min-cut theorem the
two prove each other: no flow exceeds a cut's capacity, so a flow and a cut of the same value are both optimal. The
certificate is the cut's source side; a checker confirms the flow and sums the cut from the records, without a
search. The search is Dinic's (1970): breadth-first levels in the residual graph, then a blocking flow along them, in
exact integers (float capacities are exact rationals over a common power-of-two denominator), the residual graph
built only where the search goes. The cut is the set of nodes the final residual graph reaches from the sources: the
minimum cut nearest the sources, the same for every maximum flow.

``matching`` pairs each node of a ``left`` set with at most one node outside it along a hop in scope, as many pairs as
possible (Hopcroft and Karp, 1973): distinct assignments, such as samples to instruments or claims to reviewers. The
certificate is a vertex cover (König, 1931): as many nodes as pairs, with every candidate hop touching one of them,
so no matching has more pairs. When not every left node can be paired, the uncovered left nodes S and their partners
N(S), all covered, have |N(S)| < |S| (Hall's condition fails), which says why.

An interrupted search leaves a valid flow (or matching): its value is a lower bound, never claimed optimal.
"""

from __future__ import annotations

from collections import deque
from fractions import Fraction
from types import SimpleNamespace
from typing import Any

_DIRECTION = ("forward", "reverse")


# ---------------------------------------------------------------------------------------------- max flow
def search_flow(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """Dinic's algorithm on the residual graph of the hops in scope. Each projection node v is one residual node
    (``2v``), or two when a node capacity splits it (``2v`` in, ``2v + 1`` out, an arc of the node's capacity
    between them). A node's arcs are made the first time the search reads it, each with its reverse (residual) arc,
    so that a search near the sources of a large projection builds only what it reads. A breadth-first pass labels
    levels from the sources (each node read is one expansion) and stops past the first level holding a target; a
    depth-first pass then pushes flow along level-increasing arcs until no path is left (each advance is one
    expansion). When no target is reached, the labelled nodes are the source side of a minimum cut."""
    from .engine import _sides
    sides, rel = _sides(p, plan.fwd, plan.rev), p._edge_rel
    split = plan.node_cap is not None
    terminals = set(plan.sources) | set(plan.targets)
    target_ids = {2 * t for t in plan.targets}
    head: list[int] = []                       # arc k: to head[k], residual res[k]; arc k ^ 1 is its reverse
    res: list[int] = []
    cap: list[int] = []                        # an arc's own capacity (0 for a reverse arc)
    arc_edge: list[Any] = []                   # (edge, flag) for an arc of a hop; None otherwise
    adj: dict[int, list[int]] = {}
    built: set[int] = set()
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, value=0, side=None, head=head, res=res,
                         cap=cap, arc_edge=arc_edge, adj=adj)

    def arc(x: int, y: int, c: int, edge: Any) -> None:
        for frm, to, amount, info in ((x, y, c, edge), (y, x, 0, None)):
            adj.setdefault(frm, []).append(len(head))
            head.append(to)
            res.append(amount)
            cap.append(amount)
            arc_edge.append(info)

    def inward(v: int) -> int:
        return 2 * v

    def outward(v: int) -> int:
        return 2 * v + 1 if split and v not in terminals else 2 * v

    def build(x: int) -> None:
        built.add(x)
        v = x >> 1
        if split and v not in terminals and not x & 1:
            arc(x, x + 1, plan.node_cap, None)
            return
        for lists, base, marks, ends, flag in sides:
            hs = lists[v]
            if hs is None:
                hs = lists[v] = [ei for ei in base[v] if marks[rel[ei]]]
            st.examined += len(hs)
            for ei in hs:
                w = ends[ei]
                c = plan.capacity(ei)
                if w != v and c > 0:
                    arc(x, inward(w), c, (ei, flag))

    sources = [2 * s for s in plan.sources]
    while True:
        level = {s: 0 for s in sources}
        queue = deque(sources)
        limit = None
        while queue:
            x = queue.popleft()
            if limit is not None and level[x] >= limit:
                continue
            why = ctl.stop()
            if why:
                st.interruption = why
                break
            if x not in built:
                build(x)
            for a in adj.get(x, ()):
                y = head[a]
                if res[a] > 0 and y not in level:
                    level[y] = level[x] + 1
                    if y in target_ids:
                        limit = level[y]
                    else:
                        queue.append(y)
        st.discovered = max(st.discovered, len({x >> 1 for x in level}))
        if st.interruption:
            break
        if limit is None:
            st.side = level                       # nothing reaches a target: the source side of a minimum cut
            break
        if _blocking_flow(plan, st, ctl, level, sources, target_ids):
            break
    return st


def _blocking_flow(plan: SimpleNamespace, st: SimpleNamespace, ctl: Any, level: dict, sources: list[int],
                   target_ids: set[int]) -> bool:
    """Push flow along level-increasing arcs from each source until none reaches a target (a blocking flow), with a
    current-arc pointer per node so that each arc is tried at most once per phase. True when interrupted: the flow
    pushed so far stands (a path is pushed whole or not at all)."""
    head, res, adj = st.head, st.res, st.adj
    pointer: dict[int, int] = {}
    dead: set[int] = set()
    for s in sources:
        while s not in dead:
            path: list[int] = []
            x = s
            while x not in target_ids:
                arcs = adj.get(x, ())
                i = pointer.get(x, 0)
                nxt = level[x] + 1
                while i < len(arcs):
                    a = arcs[i]
                    y = head[a]
                    if res[a] > 0 and level.get(y) == nxt and y not in dead:
                        break
                    i += 1
                pointer[x] = i
                if i < len(arcs):
                    why = ctl.stop()
                    if why:
                        st.interruption = why
                        return True
                    path.append(arcs[i])
                    x = head[arcs[i]]
                    continue
                dead.add(x)                       # no way on from x in this phase
                if not path:
                    break
                a = path.pop()
                x = head[a ^ 1]
                pointer[x] += 1
            if x in target_ids:
                push = min(res[a] for a in path)
                for a in path:
                    res[a] -= push
                    res[a ^ 1] += push
                st.value += push
    return False


def answer_flow(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    """The value, the net flow on each edge that carries any (by edge ID), and, when the search finished, the minimum
    cut: the hops from its source side to the rest (each with the capacity the request gives it) and the nodes whose
    own capacity it cuts. An interrupted answer bounds the value from above by what the sources can send and the
    targets receive."""
    from .engine import _back_sides, _number, _sides
    ids, eids, names, rel, src, dst = (p._node_ids, p._edge_ids, p._relations, p._edge_rel, p._edge_src,
                                       p._edge_dst)

    def number(field: str, x: int) -> dict:
        value, exact = _number(Fraction(x, plan.scale) if plan.rational else x, plan.rational)
        return {field: value, **({f"{field}_exact": exact} if plan.rational else {})}

    net: dict[int, int] = {}
    for k in range(0, len(st.head), 2):
        info = st.arc_edge[k]
        moved = st.cap[k] - st.res[k]
        if info is not None and moved:
            ei, flag = info
            net[ei] = net.get(ei, 0) + (moved if flag == 0 else -moved)
    flows = []
    for ei in sorted(net):
        f = net[ei]
        if f:
            a, b = (src[ei], dst[ei]) if f > 0 else (dst[ei], src[ei])
            flows.append({"edge": eids[ei], "relation": names[rel[ei]], "direction": _DIRECTION[f < 0],
                          "from": ids[a], "to": ids[b], **number("flow", abs(f))})
    complete = st.interruption is None
    answer: dict = {**number("value", st.value), "optimal": complete}
    if complete:
        answer.update(number("upper_bound", st.value))
    else:                                         # what the sources can send, or the targets receive, if less
        out_cap = sum(plan.capacity(ei) for s in plan.sources for ei, w, _ in _hops(p, _sides(p, plan.fwd, plan.rev), s)
                      if w not in plan.source_set)
        in_cap = sum(plan.capacity(ei) for t in plan.targets
                     for ei, w, _ in _hops(p, _back_sides(p, plan.fwd, plan.rev), t) if w not in plan.target_set)
        answer.update(number("upper_bound", min(out_cap, in_cap)))
    answer["flows"] = flows
    cut = cut_nodes = cert = None
    if complete:
        side = st.side
        cut, cut_nodes = [], []
        for k in range(0, len(st.head), 2):
            x, y = st.head[k ^ 1], st.head[k]
            if x in side and y not in side:
                info = st.arc_edge[k]
                if info is None:
                    cut_nodes.append({"node": ids[x >> 1], **number("capacity", st.cap[k])})
                else:
                    ei, flag = info
                    a, b = (src[ei], dst[ei]) if flag == 0 else (dst[ei], src[ei])
                    cut.append({"edge": eids[ei], "relation": names[rel[ei]], "direction": _DIRECTION[flag],
                                "from": ids[a], "to": ids[b], **number("capacity", st.cap[k])})
        cut.sort(key=lambda h: (h["edge"], h["direction"]))
        cut_nodes.sort(key=lambda h: h["node"])
        if plan.certificate:                      # a node is on the source side when its outward copy is
            split = plan.node_cap is not None
            on = sorted(x >> 1 for x in side if x & 1 or not split or (x >> 1) in plan.terminal_set)
            cert = {"kind": "cut", "source_side": [ids[v] for v in on]}
    answer.update(cut=cut, cut_nodes=cut_nodes, certificate=cert)
    return answer, []


def _hops(p: Any, sides: tuple, u: int) -> list[tuple[int, int, int]]:
    from .structure import _neighbors
    return _neighbors(p, sides, u)


# ---------------------------------------------------------------------------------------------- matching
def search_matching(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """Hopcroft and Karp: phases of a breadth-first search from the unpaired left nodes along alternating paths (a
    candidate hop out of a left node, a pair back into one), then depth-first augmentation along shortest such paths
    to unpaired partners, until none is left. A left node's candidates are its hops in scope to nodes outside the left
    set (the first hop, in canonical order, to each partner). Each left node read is one expansion."""
    from .engine import _sides
    sides, rel = _sides(p, plan.fwd, plan.rev), p._edge_rel
    left = plan.left
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, pair={}, mate={}, cand={}, free=None)
    cand, pair, mate = st.cand, st.pair, st.mate          # left -> [(right, edge, flag)], left -> that, right -> left

    def candidates(u: int) -> list:
        got = cand.get(u)
        if got is None:
            got, seen = [], set()
            for lists, base, marks, ends, flag in sides:
                hs = lists[u]
                if hs is None:
                    hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
                st.examined += len(hs)
                for ei in hs:
                    v = ends[ei]
                    if v not in left and v not in seen:
                        seen.add(v)
                        got.append((v, ei, flag))
            cand[u] = got
        return got

    order = sorted(left)
    while True:
        dist: dict[int, int] = {}
        queue = deque()
        for u in order:
            if u not in pair:
                dist[u] = 0
                queue.append(u)
        reach = None                              # the length of the shortest augmenting paths
        while queue:
            u = queue.popleft()
            if reach is not None and dist[u] >= reach:
                continue
            why = ctl.stop()
            if why:
                st.interruption = why
                break
            for v, _, _ in candidates(u):
                w = mate.get(v)
                if w is None:
                    reach = dist[u] + 1 if reach is None else reach
                elif w not in dist:
                    dist[w] = dist[u] + 1
                    queue.append(w)
        st.discovered = max(st.discovered, len(dist))
        if st.interruption or reach is None:
            st.free = dist if not st.interruption else None   # alternating reach from the unpaired: König's set
            break
        pointer: dict[int, int] = {}
        for u in order:
            if u in pair:
                continue
            path = [u]                            # left nodes along an alternating path, and the hops taken
            hops: list = []
            while path:
                x = path[-1]
                options = cand[x]
                i = pointer.get(x, 0)
                found = None
                while i < len(options):
                    v, ei, flag = options[i]
                    w = mate.get(v)
                    if (w is None and dist[x] + 1 == reach) or (w is not None and dist.get(w) == dist[x] + 1):
                        found = options[i]
                        break
                    i += 1
                pointer[x] = i
                if found is None:
                    dist[x] = -1                  # a dead end this phase
                    path.pop()
                    if hops:
                        hops.pop()
                        pointer[path[-1]] += 1
                    continue
                v = found[0]
                hops.append(found)
                w = mate.get(v)
                if w is None:                     # an unpaired partner: flip the path
                    for x2, hop in zip(path, hops):
                        pair[x2] = hop
                        mate[hop[0]] = x2
                    break
                path.append(w)
    return st


def answer_matching(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    """The pairs by left node; how many left nodes there are; when the search finished, its deficiency (the left
    nodes no maximum matching pairs) and, on request, König's vertex cover: the left nodes the unpaired cannot reach
    by alternating paths, and the partners they can."""
    ids, eids, names, rel = p._node_ids, p._edge_ids, p._relations, p._edge_rel
    pairs = []
    for u in sorted(st.pair):
        v, ei, flag = st.pair[u]
        pairs.append({"left": ids[u], "right": ids[v], "edge": eids[ei], "relation": names[rel[ei]],
                      "direction": _DIRECTION[flag]})
    complete = st.interruption is None
    answer = {"pairs": pairs, "size": len(pairs), "left_count": len(plan.left), "optimal": complete,
              "deficiency": len(plan.left) - len(pairs) if complete else None, "certificate": None}
    if complete and plan.certificate:
        reached = {u for u, d in st.free.items()}
        partners = {v for u in reached for v, _, _ in st.cand.get(u, ())}
        cover = sorted((plan.left - reached) | partners)
        answer["certificate"] = {"kind": "cover", "nodes": [ids[x] for x in cover]}
    return answer, []
