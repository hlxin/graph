"""Independent oracles: slow, plain computations over the input records that share no code with the engine (no
projection, no integer remapping, no search order), and a random case generator."""

from __future__ import annotations

import random
from fractions import Fraction

ID_POOL = ["a", "b", "c", "B", "Z", "10", "9", "é", "ä", "n-1", "n_2", "x:y", "長", "σ", "0"]
RELATIONS = ("R", "S", "T")
TYPES = ("A", "B", "C")


def hops_of(edges, scope):
    """Every hop the scope allows, as (from, to, edge id)."""
    allowed = {(s["relation"], s["direction"]) if isinstance(s, dict) else tuple(s) for s in scope}
    out = []
    for e in edges:
        if (e["relation"], "forward") in allowed:
            out.append((e["source"], e["target"], e["id"]))
        if (e["relation"], "reverse") in allowed:
            out.append((e["target"], e["source"], e["id"]))
    return out


def least_depths(edges, sources, scope, max_depth=None, strict=False):
    """Least hop count from any source, by relaxation to a fixed point. ``strict``: at least one hop (a source
    propagates at depth 0 but is listed only when a hop reaches it)."""
    hops = hops_of(edges, scope)
    seeds = set(sources)
    dist = {} if strict else {s: 0 for s in seeds}
    changed = True
    while changed:
        changed = False
        for u, v, _ in hops:
            du = 0 if (strict and u in seeds) else dist.get(u)
            if du is None:
                continue
            nd = du + 1
            if max_depth is not None and nd > max_depth:
                continue
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                changed = True
    return dist


def typed_ends(nodes, edges, sources, pattern, targets=None):
    """The nodes a walk matching the pattern can end at (within targets, when given), by layered sets."""
    types = {n if isinstance(n, str) else n["id"]: set([] if isinstance(n, str) else n.get("types") or [])
             for n in nodes}
    frontier = set(sources)
    for pos in pattern:
        want = None if pos.get("node_types") is None else set(pos["node_types"])
        frontier = {v for u, v, _ in hops_of(edges, pos["scope"])
                    if u in frontier and (want is None or types[v] & want)}
    return frontier if targets is None else frontier & set(targets)


def least_cost(nodes, edges, sources, targets, scope, costs):
    """The least exact cost from a source to a target (Bellman-Ford over exact numbers), or None."""
    (kind, table), = costs.items()
    rational = any(isinstance(x, float) for x in table.values())
    rel = {e["id"]: e["relation"] for e in edges}

    def cost(eid):
        x = table[rel[eid]] if kind == "by_relation" else table[eid]
        return Fraction(x) if rational else x

    hops = hops_of(edges, scope)
    zero = Fraction(0) if rational else 0
    dist = {s: zero for s in sources}
    for _ in range(len(nodes) + 1):
        changed = False
        for u, v, eid in hops:
            if u in dist:
                nd = dist[u] + cost(eid)
                if v not in dist or nd < dist[v]:
                    dist[v] = nd
                    changed = True
        if not changed:
            break
    found = [dist[t] for t in targets if t in dist]
    return min(found) if found else None


def random_graph(rng: random.Random, *, max_nodes=8, max_edges=18):
    ids = rng.sample(ID_POOL, rng.randint(1, max_nodes))
    nodes = []
    for nid in ids:
        types = rng.sample(TYPES, rng.randint(0, 2))
        nodes.append(nid if not types and rng.random() < 0.3 else {"id": nid, "types": types})
    edges = []
    for i in range(rng.randint(0, max_edges)):
        if edges and rng.random() < 0.2:            # a parallel edge: same ends and relation, another record
            twin = rng.choice(edges)
            src, dst, rel = twin["source"], twin["target"], twin["relation"]
        else:
            src, dst, rel = rng.choice(ids), rng.choice(ids), rng.choice(RELATIONS)
        edges.append({"id": f"e{rng.randrange(10**6):06d}-{i}", "source": src, "target": dst, "relation": rel})
    rng.shuffle(edges)
    return nodes, edges


def random_scope(rng: random.Random, relations=RELATIONS):
    pairs = [(r, d) for r in relations for d in ("forward", "reverse")]
    return [{"relation": r, "direction": d} for r, d in rng.sample(pairs, rng.randint(1, len(pairs)))]


def node_id(n):
    return n if isinstance(n, str) else n["id"]
