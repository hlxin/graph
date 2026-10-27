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

Each listed path is a witness a checker confirms; that no other simple path comes before the last one listed is
Yen's theorem, which oracle tests check, not a certificate.
"""

from __future__ import annotations

import heapq
from types import SimpleNamespace
from typing import Any


class _Stop(Exception):
    """A limit, a deadline or a cancellation stopped a search."""


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """Yen's loop over Dijkstra's searches (each settled state one expansion). An interruption keeps the paths already
    accepted: each is valid, and all are in order."""
    from .engine import _sides
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, paths=[])
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
              bound: tuple | None = None) -> tuple | None:
        """The least path from one of ``starts`` (each a key so far and a node) to a target: a key and its hops
        [(from, edge, flag, to)]. ``avoid``: nodes it may not visit; ``banned``: (node, edge, flag) hops it may not
        take as its first one; ``go_on``: a start that is a target does not end it there (that path is taken);
        ``bound``: a key no path worth finding reaches (enough candidates come before it), where the search stops."""
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
            if bound is not None and key >= bound:
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
                return key, x, path
            c0, h0, seq = key
            for ei, v, flag in hops(u):
                if v in avoid or v in done or (parent[u] is None and (u, ei, flag) in banned):
                    continue
                nkey = (c0 + cost(ei), h0 + 1, seq + (eids[ei],))
                if v not in best or nkey < best[v]:
                    best[v] = nkey
                    parent[v] = (u, ei, flag)
                    heapq.heappush(heap, (nkey, v))
        return None

    accepted: list[tuple] = []                            # (key, source, hops)
    candidates: list[tuple] = []                          # (key, source, hops, the index it left its parent at)
    known: set = set()

    def offer(found: tuple | None, deviation: int = 0) -> None:
        if found is None:
            return
        key, source, path = found
        signature = (source, tuple((ei, flag) for _, ei, flag, _ in path))
        if signature not in known:
            known.add(signature)
            heapq.heappush(candidates, (key, source, path, deviation))

    def bound() -> tuple | None:
        """The key of the last candidate that could still be listed, when there are enough: a path at or past it
        cannot be among the k."""
        need = plan.k - len(accepted)
        return heapq.nsmallest(need, candidates)[-1][0] if len(candidates) >= need > 0 else None

    try:
        offer(least([((0, 0, (ids[s],)), s) for s in plan.sources], set(), set()))
        while candidates and len(accepted) < plan.k:
            key, source, path, deviation = heapq.heappop(candidates)
            accepted.append((key, source, path))
            if len(accepted) == plan.k:
                break
            used = {s for _, s, _ in accepted}            # the virtual start: another source
            offer(least([((0, 0, (ids[s],)), s) for s in plan.sources if s not in used], set(), set(), False,
                        bound()))
            nodes = [source] + [v for *_, v in path]
            # Lawler: below the index where this path left its parent, the roots and their bans are the parent's,
            # whose spurs were searched already
            for j in range(deviation, len(path) + 1):    # spur at node j, keeping the first j hops
                root = path[:j]
                spur = nodes[j]
                same = [a_path for _, s, a_path in accepted
                        if s == source and len(a_path) >= j and [h[1:3] for h in a_path[:j]] == [h[1:3] for h in root]]
                banned = {(spur, a_path[j][1], a_path[j][2]) for a_path in same if len(a_path) > j}
                stopped = any(len(a_path) == j for a_path in same)       # the root itself is a path taken
                c_root = sum(cost(ei) for _, ei, _, _ in root)
                start_key = (c_root, j, (ids[source],) + tuple(eids[ei] for _, ei, _, _ in root))
                found = least([(start_key, spur)], set(nodes[:j]), banned, stopped, bound())
                if found is not None:
                    offer((found[0], source, root + found[2]), j)
    except _Stop as stop:
        st.interruption = str(stop)
    st.paths = accepted
    st.discovered = len(known)
    return st


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    from .engine import _number, _via
    ids = p._node_ids

    def path(entry: tuple) -> dict:
        key, source, steps = entry
        value, exact = _number(plan.exact(key[0]), plan.rational)
        return {"source": ids[source], "target": ids[steps[-1][3]] if steps else ids[source], "length": len(steps),
                "hops": [{**_via(p, u, ei, flag), "to": ids[v], "cost": plan.given(ei)} for u, ei, flag, v in steps],
                "cost": value, **({"cost_exact": exact} if plan.rational else {})}
    complete = st.interruption is None
    paths = [path(e) for e in st.paths]
    return ({"paths": paths, "count": len(paths), "exhausted": (len(paths) < plan.k) if complete else None},
            [RANKING_NOTE] + ([EXHAUSTED_NOTE] if complete and len(paths) < plan.k else []))


RANKING_NOTE = ("Each path is a witness (a simple path from a source to a target, at the cost it states); they come in "
                "the order of (cost, hops, start node and edge IDs). That no other simple path comes before the last "
                "one listed is Yen's theorem, not a certificate.")
EXHAUSTED_NOTE = "Fewer than k simple paths exist: these are all of them, in order."
