"""d-separation: whether a causal graph makes two sets of nodes independent given a third (Pearl, 1988; Geiger,
Verma and Pearl, 1990).

A hop in scope from u to v reads as "u is a direct cause (a parent) of v". A trail between two nodes follows hops
either way, and is active given a set Z when every node on it where both neighbouring hops point in (a collider,
u -> v <- w) is in Z or has a descendant in Z, and every other node on it is not in Z. X and Y are d-separated given Z
when no trail between them is active: in every distribution the directed acyclic graph models, X and Y are then
independent given Z, and a single active trail is a dependence the graph allows. The question behind adjustment for
confounding (which variables to condition on), causal claims in a corpus, and the structure of an experiment.

The search is the Bayes-ball reachability of Shachter (1998), as Koller and Friedman write it (Algorithm 3.1): first
Z and its ancestors, then a breadth-first search over states (node, how the trail arrived: from a child, going up,
or from a parent, going down) from each node of X as if arrived from a child. A trail may pass a node not in Z in
any way except down-then-up, and may turn from down to up (a collider) only at Z or an ancestor of Z. It reaches
exactly the nodes an active trail reaches. On a graph with cycles the same test is run; its causal reading needs an
acyclic one.

Witness and certificate. Not separated: an active trail from a node of X to one of Y, hop by hop, with a directed
path from each collider on it down to Z (empty when the collider is in Z); a checker confirms each step from the
records. Separated: on request, the states the search visited and the set it used as Z's ancestors; a checker
confirms the ancestors are closed under parents and hold Z, the states hold every start and are closed under the
moves, and no node of Y is among them, so no active trail exists.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Any

_DIRECTION = ("forward", "reverse")
UP, DOWN = 0, 1


def search(p: Any, plan: SimpleNamespace, ctl: Any) -> SimpleNamespace:
    """Z's ancestors (each node one expansion, with the hop each takes toward Z), then the Bayes-ball states from X
    (each state one expansion) until a node of Y is reached or none is left."""
    from .engine import _back_sides, _sides
    from .structure import _neighbors
    st = SimpleNamespace(interruption=None, examined=0, discovered=0, found=None, prev=None, toward=None,
                         ancestors=None, visited=None)
    children, parents = _sides(p, plan.fwd, plan.rev), _back_sides(p, plan.fwd, plan.rev)
    given, targets = set(plan.given), set(plan.y)
    ancestors, toward = set(given), {}
    queue = deque(sorted(given))
    while queue:
        v = queue.popleft()
        why = ctl.stop()
        if why:
            st.interruption = why
            return st
        for ei, u, flag in _neighbors(p, parents, v):
            st.examined += 1
            if u not in ancestors:
                ancestors.add(u)
                toward[u] = (ei, flag, v)
                queue.append(u)
    prev: dict[tuple, Any] = {}
    states = deque()
    for x in plan.x:
        prev[(x, UP)] = None
        states.append((x, UP))
    while states:
        v, way = states.popleft()
        if v in targets:
            st.found = (v, way)
            break
        why = ctl.stop()
        if why:
            st.interruption = why
            return st
        moves = []
        if v not in given:
            moves += [((w, DOWN), (ei, flag, v, w)) for ei, w, flag in _neighbors(p, children, v)]
            if way == UP:
                moves += [((u, UP), (ei, flag, u, v)) for ei, u, flag in _neighbors(p, parents, v)]
        if way == DOWN and v in ancestors:
            moves += [((u, UP), (ei, flag, u, v)) for ei, u, flag in _neighbors(p, parents, v)]
        st.examined += len(moves)
        for state, hop in moves:
            if state not in prev:
                prev[state] = ((v, way), hop)
                states.append(state)
    st.discovered = len(prev)
    st.prev, st.toward, st.ancestors = prev, toward, ancestors
    return st


def answer(p: Any, plan: SimpleNamespace, st: SimpleNamespace) -> tuple[dict, list[str]]:
    """Separated or not; the active trail and its colliders' paths down to Z when not; on request, the certificate
    when separated."""
    ids, eids, names, rel = p._node_ids, p._edge_ids, p._relations, p._edge_rel

    def hop(ei: int, flag: int, parent: int, child: int) -> dict:
        return {"edge": eids[ei], "relation": names[rel[ei]], "direction": _DIRECTION[flag], "from": ids[parent],
                "to": ids[child]}
    out: dict = {"separated": None, "trail": None, "colliders": None, "certificate": None}
    if st.interruption:
        return out, []
    if st.found is None:
        out["separated"] = True
        if plan.certificate:
            ways: dict[int, list[bool]] = {}
            for v, way in st.prev:
                ways.setdefault(v, [False, False])[way] = True
            out["certificate"] = {"kind": "bayes_ball", "ancestors": [ids[u] for u in sorted(st.ancestors)],
                                  "states": [{"node": ids[v], "up": ways[v][UP], "down": ways[v][DOWN]}
                                             for v in sorted(ways)]}
        return out, []
    steps, state = [], st.found
    while st.prev[state] is not None:
        before, (ei, flag, parent, child) = st.prev[state]
        steps.append({**hop(ei, flag, parent, child), "travel": "down" if state[1] == DOWN else "up"})
        state = before
    steps.reverse()
    colliders = []
    for a, b in zip(steps, steps[1:]):
        if a["travel"] == "down" and b["travel"] == "up":       # in from a parent, out to a parent
            v = p._node_index[a["to"]]
            path = []
            while v in st.toward:
                ei, flag, child = st.toward[v]
                path.append(hop(ei, flag, v, child))
                v = child
            colliders.append({"node": a["to"], "to_given": path})
    out.update(separated=False, trail=steps, colliders=colliders)
    return out, []
