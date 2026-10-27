"""Reachability between many pairs at once, from a 2-hop index: does each source reach its target, and by what walk.

The index is a pair of labels per node (Cohen, Halperin, Kaplan and Zwick, 2003): hubs the node reaches (its out-label)
and hubs that reach it (its in-label), so that a source reaches a target exactly when the source's out-label and the
target's in-label share a hub. It is built by pruned landmark labelling (Akiba, Iwata and Yoshida, 2013; for
reachability, Yano, Akiba, Iwata and Yoshida, 2013): the nodes in a fixed order, the most connected first (the
product of one more than their in- and out-degrees in scope, descending; then the node ID), each in turn a hub: a
breadth-first search backwards from it gives the hub to every node that reaches it, and one forwards to every node it
reaches, except that a node already covered (one whose labels share an earlier hub with the hub's) is neither labelled
nor searched through. A node is its own hub, so a node reaches itself by the empty walk. Every entry keeps the hop
its search came by, toward the hub (out) or from it (in): following them gives a walk from the source through the
common hub to the target, from which any loop is cut, so the witness is a simple path.

The index is a function of the projection and the scope alone, built once and kept on the projection. Every request
is charged what building it costs, one expansion per node read and one per label entry, whether it is built then or
was kept: the same request on the same projection then spends the same expansions and gets the same result, cold or
warm; and a budget smaller than the index is exhausted the same way both times (a kept index only saves the time). A
deadline or a cancellation that stops the building keeps nothing; a projection keeps the last four indexes built.

Answering a pair costs one expansion, plus one per entry of the smaller of the two labels read, plus one per hop of
the walk through the hub that its witness is cut from (loops included). A negative is proven, on request, by the closure of its source: every node the source reaches, a set
that holds the source and not the target, from which every hop in scope stays inside, one expansion per node.
"""

from __future__ import annotations

from array import array
from types import SimpleNamespace
from typing import Any

_KEPT = 4                                # indexes a projection keeps (by scope); the oldest goes first


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """The index (kept, or built and kept), then each pair in canonical order, then the closures of the sources with a
    negative. An interruption keeps the pairs answered: a prefix of them, each decided."""
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, index=None, answered=[], closures=None)
    index = _index(p, plan, ctl, st)
    if index is None:
        return st
    st.index = index
    out_labels, in_labels = index.out_labels, index.in_labels
    for s, t in plan.pairs:
        if s == t:
            why = ctl.stop()
            if why:
                st.interruption = why
                return st
            st.answered.append((s, t, 0, []))
            continue
        a, b = out_labels[s], in_labels[t]
        if len(a) > len(b):
            small, other = b, a
        else:
            small, other = a, b
        why = ctl.take(1 + len(small))
        if why:
            st.interruption = why
            return st
        common = [h for h in small if h in other]
        if not common:
            st.answered.append((s, t, None, None))
            continue
        hub = min(common)
        hops = None
        if plan.witnesses:
            try:
                hops = _walk(p, index, s, t, hub, ctl)
            except _Stopped as stop:                               # the pair is not listed unfinished
                st.interruption = str(stop)
                return st
        st.answered.append((s, t, hub, hops))
    if plan.certificate:
        st.closures = {}
        sides, rel = _sides_of(p, plan)
        for s in sorted({s for s, _, hub, _ in st.answered if hub is None}):
            seen, todo = {s}, [s]
            while todo:
                why = ctl.stop()
                if why:
                    st.interruption, st.closures = why, None
                    return st
                u = todo.pop()
                for ei, v, _ in _hops(sides, rel, u, st):
                    if v not in seen:
                        seen.add(v)
                        todo.append(v)
            st.closures[s] = seen
    return st


def _sides_of(p: Any, plan: SimpleNamespace) -> tuple:
    from .engine import _sides
    return _sides(p, plan.fwd, plan.rev), p._edge_rel


def _hops(sides: tuple, rel: Any, u: int, st: SimpleNamespace | None = None) -> list[tuple[int, int, int]]:
    """(edge, the node it leads to, flag) for every hop in scope from u, forward hops first."""
    out = []
    for lists, base, marks, ends, flag in sides:
        hs = lists[u]
        if hs is None:
            hs = lists[u] = [ei for ei in base[u] if marks[rel[ei]]]
        if st is not None:
            st.examined += len(hs)
        out.extend((ei, ends[ei], flag) for ei in hs)
    return out


def _index(p: Any, plan: SimpleNamespace, ctl: Any, st: SimpleNamespace) -> SimpleNamespace | None:
    """The index of the projection and the scope: kept, its cost charged; or built, one expansion per entry, and kept
    when complete. None, with ``st.interruption``, when the charge or the building stops."""
    kept = p._memo.setdefault("reach_index", {})
    key = (tuple(plan.fwd), tuple(plan.rev))
    index = kept.get(key)
    n = len(p._node_ids)
    if index is not None:                                          # charged and counted as building it now
        before = ctl.expansions
        why = ctl.take(n + index.entries)
        read = min(ctl.expansions - before, n)                     # the nodes whose hops a build would have read
        st.examined, st.discovered = index.read[read], read
        if why:
            st.interruption = why
            return None
        return index
    from .engine import _back_sides, _sides
    rel = p._edge_rel
    outs: list = []                                                # per node: (hop, next node)
    ins: list = []                                                 # per node: (hop, previous node)
    read = array("q", [0])                                         # hops read before each node, cumulative
    for u in range(n):                                             # one expansion per node read, before reading it
        why = ctl.stop()
        if why:
            st.interruption = why
            return None
        if u == 0:
            forward, backward = _sides(p, plan.fwd, plan.rev), _back_sides(p, plan.fwd, plan.rev)
        outs.append([(2 * ei + flag, v) for ei, v, flag in _hops(forward, rel, u)])
        ins.append([(2 * ei + flag, w) for ei, w, flag in _hops(backward, rel, u)])
        st.examined += len(outs[u]) + len(ins[u])
        st.discovered = u + 1
        read.append(st.examined)
    order = sorted(range(n), key=lambda v: (-(len(ins[v]) + 1) * (len(outs[v]) + 1), v))
    # hub rank -> the hop (2 * edge + flag, one shared int per hop: an entry allocates nothing) toward the hub (out)
    # or from it (in); None at the hub itself
    out_labels: list[dict] = [{} for _ in range(n)]
    in_labels: list[dict] = [{} for _ in range(n)]

    def covered(a: dict, b: dict) -> bool:
        return not a.keys().isdisjoint(b.keys())                    # in C, over the smaller label
    entries = 0
    for r, h in enumerate(order):
        for labels, steps, backwards in ((out_labels, ins, True), (in_labels, outs, False)):
            why = ctl.stop()
            if why:
                st.interruption = why
                return None
            labels[h][r] = None
            entries += 1
            queue, seen = [h], {h}
            for x in queue:
                for code, u in steps[x]:
                    if u in seen:
                        continue
                    seen.add(u)
                    if covered(out_labels[u], in_labels[h]) if backwards else covered(out_labels[h], in_labels[u]):
                        continue
                    why = ctl.stop()
                    if why:
                        st.interruption = why
                        return None
                    labels[u][r] = code
                    entries += 1
                    queue.append(u)
    index = SimpleNamespace(out_labels=out_labels, in_labels=in_labels, order=order, entries=entries, read=read)
    while len(kept) >= _KEPT:
        kept.pop(next(iter(kept)), None)
    kept[key] = index
    return index


class _Stopped(Exception):
    """A limit, a deadline or a cancellation stopped a witness's walk."""


def _walk(p: Any, index: SimpleNamespace, s: int, t: int, hub: int, ctl: Any) -> list[tuple[int, int, int, int]]:
    """The witness from s through the hub to t, as hops (from, edge, flag, to), loops cut: a simple path. Each hop of
    the walk through the hub, a loop's included, is one expansion, charged before it is followed."""
    src, dst = p._edge_src, p._edge_dst

    def step() -> None:
        why = ctl.stop()
        if why:
            raise _Stopped(why)
    hops = []
    u = s
    while True:                                                    # s to the hub, by the out-labels' hops
        code = index.out_labels[u][hub]
        if code is None:
            break
        step()
        ei, flag = code >> 1, code & 1
        v = src[ei] if flag else dst[ei]                           # a hop forward leads to its edge's target
        hops.append((u, ei, flag, v))
        u = v
    back = []
    v = t
    while True:                                                    # the hub to t, read back from t
        code = index.in_labels[v][hub]
        if code is None:
            break
        step()
        ei, flag = code >> 1, code & 1
        u = dst[ei] if flag else src[ei]                           # a hop forward comes from its edge's source
        back.append((u, ei, flag, v))
        v = u
    hops.extend(reversed(back))
    seen: dict[int, int] = {s: 0}                                  # cut every loop: keep the walk's first visit
    path: list = []
    for hop in hops:
        at = seen.get(hop[3])
        if at is not None:
            for dropped in path[at:]:
                del seen[dropped[3]]
            del path[at:]
            continue
        path.append(hop)
        seen[hop[3]] = len(path)
    return path


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    from .engine import _via
    ids = p._node_ids
    pairs = []
    for s, t, hub, hops in st.answered:
        entry = {"source": ids[s], "target": ids[t], "reachable": hub is not None, "witness": None}
        if hub is not None and plan.witnesses:
            entry["witness"] = {"source": ids[s], "target": ids[t], "length": len(hops),
                                "hops": [{**_via(p, u, ei, flag), "to": ids[v]} for u, ei, flag, v in hops]}
        pairs.append(entry)
    complete = st.interruption is None
    cert = None
    if complete and plan.certificate:
        cert = {"kind": "closures", "closures": [{"source": ids[s], "nodes": [ids[x] for x in sorted(seen)]}
                                                 for s, seen in sorted(st.closures.items())]}
    index = None if st.index is None else {"entries": st.index.entries,
                                           "largest": max((len(a) + len(b) for a, b in
                                                           zip(st.index.out_labels, st.index.in_labels)), default=0)}
    reached = sum(1 for e in pairs if e["reachable"])
    notes = [PAIRS_NOTE] if pairs else []
    return ({"pairs": pairs, "count": len(pairs), "reachable": reached, "index": index, "certificate": cert}, notes)


PAIRS_NOTE = ("Each pair listed is decided by the index: a source reaches its target exactly when the source's "
              "out-label and the target's in-label share a hub. A reachable pair's witness is a simple path through "
              "that hub; an unreachable pair is proven only by the certificate's closure of its source.")
