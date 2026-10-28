"""The k shortest simple paths: the best few alternatives, not only the best one (alternative reaction pathways,
backup routes, the next most likely derivations).

A path here is simple: it starts at a source, ends at a target and visits no node twice (other sources and targets
may lie on it). Paths are ranked in a strict order: by cost, then by number of hops, then by the start node's ID and
the sequence of edge IDs. Costs are as a shortest path's, non-negative and exact; without costs every hop costs 1.

Yen's algorithm (1971): the first path is the least one; every accepted path is then split at each of its nodes, and
from that spur node the least path that keeps the accepted prefix (the root), avoids the root's other nodes (so the
result stays simple) and leaves the spur node by none of the hops that accepted paths with the same root took there,
is a candidate; the least candidate is the next path. Stopping is one more way to leave a spur node: a path may end
at a target on the way (a prefix of an accepted path), or go on past a target where an accepted path ended, so the
spur at a node that is a target excludes stopping there exactly when that root was accepted. A virtual start before
the sources makes the choice of source the first step, so several sources work the same way. Each least path comes from Dijkstra's search on the full order:
extending a path extends its key the same way for every path to the same node, so the least path to a node extends
a least path to its predecessor, and the first target settled ends the least path.

Each listed path is a witness a checker confirms. With ``certificate: true`` a complete answer also proves that no
other simple path comes before the last one listed (or, when fewer than k exist, that there are no others), from two
parts. The potentials are, for every node that reaches a target, a lower bound on the (cost, hops) of reaching one:
no hop in scope lowers them by more than its own (cost, 1), every target is at (0, 0), and a node not listed reaches
none. The listed paths' prefixes form a tree, and every simple path not listed leaves it somewhere: at a prefix, by
a hop no listed path with that prefix takes (or, at the start, from a source none starts at). For each prefix, the
walks that leave it this way and avoid its nodes are explored as far as the potentials allow a walk to still come
before the last path: each node so reached is labelled with its least (cost, hops, side), where side says whether,
on a tie of cost and hops, the walk's IDs come before the last path's or after them (fixed by where it left the
tree). No labelled node is a target, so no unlisted path comes before the last one. A checker confirms each label by
its hops alone, without searching.
"""

from __future__ import annotations

import heapq
from types import SimpleNamespace
from typing import Any


class _Stop(Exception):
    """A limit, a deadline or a cancellation stopped a search."""


class _Seq:
    """A sequence of IDs that one more ID extends without copying: its last ID and the sequence before it (None for
    the empty one). Sequences compare as the tuples they stand for; two of the same length, as those on a tie of cost
    and hops are, compare by walking up both until they meet, so a comparison costs their differing ends, and
    extending a path costs one cell, not its length."""

    __slots__ = ("last", "up", "size")

    def __init__(self, last: Any, up: _Seq | None) -> None:
        self.last, self.up, self.size = last, up, 1 + (up.size if up is not None else 0)

    def ids(self) -> tuple:
        out, x = [], self
        while x is not None:
            out.append(x.last)
            x = x.up
        return tuple(reversed(out))

    def _cmp(self, other: _Seq) -> int:
        if self.size != other.size:
            a, b = self.ids(), other.ids()
            return (a > b) - (a < b)
        x, y, first = self, other, 0
        while x is not y:                                 # the topmost difference decides
            if x.last != y.last:
                first = 1 if x.last > y.last else -1
            x, y = x.up, y.up
        return first

    def __lt__(self, other: _Seq) -> bool:
        return self._cmp(other) < 0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Seq) and self._cmp(other) == 0

    __hash__ = None  # type: ignore[assignment]


def _ids(seq: _Seq | None) -> tuple:
    return () if seq is None else seq.ids()


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """Yen's loop over Dijkstra's searches (each settled state one expansion). An interruption keeps the paths already
    accepted: each is valid, and all are in order."""
    from .engine import _sides
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, paths=[], certificate=None)
    sides, rel = _sides(p, plan.fwd, plan.rev), p._edge_rel
    eids, ids = p._edge_ids, p._node_ids
    targets, cost = plan.targets, plan.cost

    def hops(u: int) -> list[tuple[int, int, int]]:
        out = []
        for lists, base, marks, ends, flag in sides:
            hs = lists[u]
            if hs is None:
                hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
            st.examined += len(hs)
            out.extend((ei, ends[ei], flag) for ei in hs)
        return out

    def least(starts: list[tuple[tuple, int]], avoid: set, banned: set, go_on: bool = False,
              bound: tuple | None = None, root: Any = None) -> tuple | None:
        """The least path from one of ``starts`` (each a key so far and a node) to a target: a key and its hops
        [(from, edge, flag, to)]. ``avoid``: nodes it may not visit; ``banned``: (node, edge, flag) hops it may not
        take as its first one; ``go_on``: a start that is a target does not end it there (that path is taken);
        ``bound``: a key no path worth finding reaches (enough candidates come before it), where the search stops.
        ``root``: when every start extends one prefix, a function giving that prefix's IDs; the keys then hold only
        the IDs after it (all of them share it, so they compare the same), and the full IDs are built only to compare
        a tie of cost and hops with the bound, and for the path found."""
        prefix: list = []

        def full(seq: _Seq | None) -> tuple:
            if root is None:
                return _ids(seq)
            if not prefix:
                prefix.append(root())
            return prefix[0] + _ids(seq)

        def past(key: tuple) -> bool:
            if (key[0], key[1]) != (bound[0], bound[1]):
                return (key[0], key[1]) > (bound[0], bound[1])
            return full(key[2]) >= bound[2]
        best: dict[int, tuple] = {}
        parent: dict[int, tuple] = {}
        heap = []
        for key, u in starts:
            if u not in avoid and (u not in best or key < best[u]):
                best[u] = key
                parent[u] = None
                heapq.heappush(heap, (key, u))
        done: set[int] = set()
        while heap:
            key, u = heapq.heappop(heap)
            if u in done or key != best[u]:
                continue
            del best[u]                                   # settled: its key (and its prefix of edge IDs) can go
            if bound is not None and past(key):
                return None
            why = ctl.stop()
            if why:
                raise _Stop(why)
            done.add(u)
            if u in targets and not (go_on and parent[u] is None):
                path, x = [], u
                while parent[x] is not None:
                    w, ei, flag = parent[x]
                    path.append((w, ei, flag, x))
                    x = w
                path.reverse()
                return (key[0], key[1], full(key[2])), x, path
            c0, h0, seq = key
            for ei, v, flag in hops(u):
                if v in avoid or v in done or (parent[u] is None and (u, ei, flag) in banned):
                    continue
                nkey = (c0 + cost(ei), h0 + 1, _Seq(eids[ei], seq))
                if v not in best or nkey < best[v]:
                    best[v] = nkey
                    parent[v] = (u, ei, flag)
                    heapq.heappush(heap, (nkey, v))
        return None

    accepted: list[tuple] = []                            # (key, source, hops)
    candidates: list[tuple] = []                          # (key, source, hops, the index it left its parent at)
    known: set = set()
    cached: list = []                                     # the bound, until a candidate comes or goes

    def offer(found: tuple | None, deviation: int = 0) -> None:
        if found is None:
            return
        key, source, path = found
        signature = (source, tuple((ei, flag) for _, ei, flag, _ in path))
        if signature not in known:
            known.add(signature)
            heapq.heappush(candidates, (key, source, path, deviation))
            cached.clear()

    def bound() -> tuple | None:
        """The key of the last candidate that could still be listed, when there are enough: a path at or past it
        cannot be among the k."""
        if not cached:
            need = plan.k - len(accepted)
            cached.append(heapq.nsmallest(need, candidates)[-1][0] if len(candidates) >= need > 0 else None)
        return cached[0]

    tree: dict = {}                                       # the accepted paths' prefixes: [next hops, a path stops]
    try:
        offer(least([((0, 0, _Seq(ids[s], None)), s) for s in plan.sources], set(), set()))
        while candidates and len(accepted) < plan.k:
            key, source, path, deviation = heapq.heappop(candidates)
            cached.clear()
            accepted.append((key, source, path))
            at = tree.setdefault(source, [{}, False])
            for _, ei, flag, _ in path:
                at = at[0].setdefault((ei, flag), [{}, False])
            at[1] = True
            if len(accepted) == plan.k:
                break
            used = {s for _, s, _ in accepted}            # the virtual start: another source
            offer(least([((0, 0, _Seq(ids[s], None)), s) for s in plan.sources if s not in used], set(), set(),
                        False, bound()))
            nodes = [source] + [v for *_, v in path]
            # Lawler: below the index where this path left its parent, the roots and their bans are the parent's,
            # whose spurs were searched already. The root's cost, its nodes and its place in the tree of accepted
            # prefixes (whose next hops are the bans) grow by one hop per spur, not rebuilt
            at = tree[source]
            for _, ei, flag, _ in path[:deviation]:
                at = at[0][(ei, flag)]
            avoid = set(nodes[:deviation])
            c_root = sum(cost(ei) for _, ei, _, _ in path[:deviation])
            for j in range(deviation, len(path) + 1):    # spur at node j, keeping the first j hops
                if j > deviation:
                    _, ei, flag, _ = path[j - 1]
                    at = at[0][(ei, flag)]
                    avoid.add(nodes[j - 1])
                    c_root += cost(ei)
                spur = nodes[j]
                banned = {(spur, ei, flag) for ei, flag in at[0]}
                stopped = at[1]                           # the root itself is a path taken

                def root(j: int = j) -> tuple:
                    return (ids[source],) + tuple(eids[ei] for _, ei, _, _ in path[:j])
                found = least([((c_root, j, None), spur)], avoid, banned, stopped, bound(), root)
                if found is not None:
                    offer((found[0], source, path[:j] + found[2]), j)
        if plan.certificate:
            st.certificate = certify(p, plan, accepted, hops, ctl)
    except _Stop as stop:
        st.interruption = str(stop)
        st.certificate = None
    st.paths = accepted
    st.discovered = len(known)
    return st


def certify(p: Any, plan: SimpleNamespace, accepted: list[tuple], hops: Any, ctl: Any) -> dict:
    """The certificate of a complete ranking: potentials by a reverse search from the targets, then, for the start
    and every prefix of the listed paths, the walks that leave the prefix tree there, explored by A* with those
    potentials while they could still come before the last path (all of them, when fewer than k paths exist). Each
    settled node is one expansion."""
    from .engine import _back_sides
    ids, eids, targets, cost = p._node_ids, p._edge_ids, plan.targets, plan.cost
    rel = p._edge_rel
    back = _back_sides(p, plan.fwd, plan.rev)

    def into(v: int) -> list[tuple[int, int]]:
        out = []
        for lists, base, marks, ends, _ in back:
            hs = lists[v]
            if hs is None:
                hs = lists[v] = [ei for ei in base[v] if marks[rel[ei]]]
            out.extend((ei, ends[ei]) for ei in hs)
        return out

    pi: dict[int, tuple] = {}                             # node: least (cost, hops) to a target
    heap = [((0, 0), t) for t in sorted(targets)]
    best = {t: (0, 0) for t in targets}
    while heap:
        key, v = heapq.heappop(heap)
        if v in pi or best[v] != key:
            continue
        why = ctl.stop()
        if why:
            raise _Stop(why)
        pi[v] = key
        for ei, u in into(v):
            nkey = (key[0] + cost(ei), key[1] + 1)
            if u not in pi and (u not in best or nkey < best[u]):
                best[u] = nkey
                heapq.heappush(heap, (nkey, u))

    full = len(accepted) == plan.k
    if full:
        last_key, last_source, last_path = accepted[-1]
        tau = (last_key[0], last_key[1], 1)               # the last path: cost, hops, and walks after it on a tie
        last_seq = last_key[2]
    else:
        tau = last_seq = None

    def pruned(val: tuple, v: int) -> bool:
        """Whether every walk through ``v`` with ``val`` so far comes after the last path (or reaches no target)."""
        bound = pi.get(v)
        if bound is None:
            return True
        return tau is not None and ahead(val, bound) >= tau

    explored = []

    def ahead(val: tuple, bound: tuple) -> tuple:
        return val[0] + bound[0], val[1] + bound[1], val[2]

    def explore(where: tuple, starts: list[tuple[tuple, int]], avoid: set) -> None:
        best: dict[int, tuple] = {}
        heap = []
        for val, v in starts:
            if not pruned(val, v) and (v not in best or val < best[v]):
                best[v] = val
                heapq.heappush(heap, (ahead(val, pi[v]), val, v))
        labels: dict[int, tuple] = {}
        while heap:
            _, val, v = heapq.heappop(heap)
            if v in labels or best[v] != val:
                continue
            why = ctl.stop()
            if why:
                raise _Stop(why)
            if v in targets:
                raise AssertionError(f"k shortest paths: a path reaching {ids[v]} the ranking missed")
            labels[v] = val
            for ei, z, _ in hops(v):
                if z in avoid or z in labels:
                    continue
                nval = (val[0] + cost(ei), val[1] + 1, val[2])
                if not pruned(nval, z) and (z not in best or nval < best[z]):
                    best[z] = nval
                    heapq.heappush(heap, (ahead(nval, pi[z]), nval, z))
        if labels:
            explored.append((where, labels))

    used = {s for _, s, _ in accepted}
    explore((None, None), [((0, 0, int(tau is not None and ids[s] > last_seq[0])), s)
                           for s in plan.sources if s not in used], set())
    roots: dict = {}                                      # the prefix tree: [children by (edge, flag), explored]
    along = []
    for _, source, path in accepted:
        at = roots.setdefault(source, [{}, False])
        nodes = [at]
        for _, ei, flag, _ in path:
            at = at[0].setdefault((ei, flag), [{}, False])
            nodes.append(at)
        along.append(nodes)
    for i, (_, source, path) in enumerate(accepted):
        nodes = [source] + [v for *_, v in path]
        same = 0                                          # how many leading IDs it shares with the last path
        if tau is not None and ids[source] == last_seq[0]:
            same = 1
            while same <= len(path) and same < len(last_seq) and eids[path[same - 1][1]] == last_seq[same]:
                same += 1
        avoid: set = set()
        c_root = 0
        for j, tree in enumerate(along[i]):
            avoid.add(nodes[j])
            if j:
                c_root += cost(path[j - 1][1])
            if tree[1]:
                continue                                  # a prefix an earlier path explored
            tree[1] = True
            if tau is None:
                side = lambda ei: 0                        # noqa: E731 - no last path to tie with
            elif j + 1 <= same:                           # a prefix of the last path: the next edge decides
                side = ((lambda ei: 1) if j == len(last_path)
                        else (lambda ei, nxt=last_seq[j + 1]: int(eids[ei] > nxt)))
            else:                                         # it already differs from the last path's IDs
                mine = ids[source] if same == 0 else eids[path[same - 1][1]]
                fixed = int(same >= len(last_seq) or mine > last_seq[same])
                side = lambda ei, fixed=fixed: fixed       # noqa: E731
            starts = [((c_root + cost(ei), j + 1, side(ei)), v) for ei, v, flag in hops(nodes[j])
                      if v not in avoid and (ei, flag) not in tree[0]]
            explore((i, j), starts, avoid)
    return {"pi": pi, "explored": explored}


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    from .engine import _number, _via
    ids = p._node_ids

    def path(entry: tuple) -> dict:
        key, source, steps = entry
        value, exact = _number(plan.exact(key[0]), plan.rational)
        return {"source": ids[source], "target": ids[steps[-1][3]] if steps else ids[source], "length": len(steps),
                "hops": [{**_via(p, u, ei, flag), "to": ids[v], "cost": plan.given(ei)} for u, ei, flag, v in steps],
                "cost": value, **({"cost_exact": exact} if plan.rational else {})}
    def exact(x: Any) -> Any:
        return _number(plan.exact(x), True)[1] if plan.rational else x

    complete = st.interruption is None
    paths = [path(e) for e in st.paths]
    cert = None
    if complete and st.certificate is not None:
        sides = (None, "before", "after") if len(paths) == plan.k else (None, None, None)
        cert = {"kind": "deviations",
                "potentials": [{"node": ids[v], "cost": exact(c), "hops": h} for v, (c, h) in sorted(
                    st.certificate["pi"].items())],
                "explored": [{"path": i, "at": j, "labels": [
                    {"node": ids[v], "cost": exact(c), "hops": h, "side": sides[1 + b]}
                    for v, (c, h, b) in sorted(labels.items())]} for (i, j), labels in st.certificate["explored"]]}
    return ({"paths": paths, "count": len(paths), "exhausted": (len(paths) < plan.k) if complete else None,
             "certificate": cert},
            ([CERTIFIED_NOTE] if cert else [RANKING_NOTE]) + ([EXHAUSTED_NOTE] if complete and len(paths) < plan.k
                                                              else []))


RANKING_NOTE = ("Each path is a witness (a simple path from a source to a target, at the cost it states); they come in "
                "the order of (cost, hops, start node and edge IDs). That no other simple path comes before the last "
                "one listed is Yen's theorem, proven only when a certificate is asked for.")
CERTIFIED_NOTE = ("Each path is a witness, and the certificate proves the ranking: its potentials bound the cost of "
                  "reaching a target from every node, and its labels show that no walk leaving the listed paths' "
                  "prefixes reaches a target before the last path (or at all, when fewer than k paths exist).")
EXHAUSTED_NOTE = "Fewer than k simple paths exist: these are all of them, in order."
