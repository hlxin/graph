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


def closure_up(parents):
    """Every name's ancestors in a hierarchy, by repeated expansion."""
    up = {}
    for n in parents or {}:
        seen, todo = set(), list(parents[n])
        while todo:
            x = todo.pop()
            if x not in seen:
                seen.add(x)
                todo += (parents or {}).get(x, [])
        up[n] = seen
    return up


def expand_scope(scope, relation_parents, relations=RELATIONS):
    """A scope over a relation hierarchy, written out: an entry covers every relation that has it above."""
    up = closure_up(relation_parents)
    names = set(relations) | {s["relation"] if isinstance(s, dict) else s[0] for s in scope}
    out = []
    for r in sorted(names):
        for s in scope:
            rel, direction = (s["relation"], s["direction"]) if isinstance(s, dict) else s
            if rel == r or rel in up.get(r, ()):
                out.append({"relation": r, "direction": direction})
    return out


def closed_nodes(nodes, type_parents):
    """Nodes with every ancestor of their declared types."""
    up = closure_up(type_parents)
    out = []
    for n in nodes:
        types = set() if isinstance(n, str) else set(n.get("types") or [])
        out.append({"id": node_id(n), "types": sorted(types.union(*(up.get(t, ()) for t in types)))})
    return out


def pattern_rows(nodes, edges, match, steps, type_parents=None, relation_parents=None, relations=RELATIONS):
    """Every row of a tree pattern, in canonical order, by plain nested loops over the edges and no pruning: roots by
    ascending ID, then per step its forward hops by edge ID, then its reverse hops, one per (edge, node)."""
    types = {n["id"]: set(n["types"]) for n in closed_nodes(nodes, type_parents)}
    edges = sorted(edges, key=lambda e: e["id"])
    names = set(relations) | {e["relation"] for e in edges}

    def admits(spec, v):
        return (spec.get("node_types") is None or bool(types[v] & set(spec["node_types"]))) and \
            (spec.get("nodes") is None or v in set(spec["nodes"]))

    def hops(step, u):
        scope = expand_scope(step["scope"], relation_parents or {}, names)
        fwd = {x["relation"] for x in scope if x["direction"] == "forward"}
        rev = {x["relation"] for x in scope if x["direction"] == "reverse"}
        out = [(e["id"], e["target"]) for e in edges if e["source"] == u and e["relation"] in fwd]
        out += [(e["id"], e["source"]) for e in edges if e["target"] == u and e["relation"] in rev]
        kept = []
        for eid, w in out:
            if admits(step, w) and (step.get("edges") is None or eid in set(step["edges"])) and (eid, w) not in kept:
                kept.append((eid, w))
        return kept

    rows = []

    def extend(i, bind, links):
        if i == len(steps):
            rows.append(({**bind}, list(links)))
            return
        s = steps[i]
        u = bind[s["from"]]
        hs = [] if u is None else hops(s, u)
        if not hs:
            if s.get("optional"):
                extend(i + 1, {**bind, s["var"]: None}, links + [None])
            return
        for eid, w in hs:
            extend(i + 1, {**bind, s["var"]: w}, links + [eid])

    for root in sorted(types):
        if admits(match, root):
            extend(0, {match["var"]: root}, [])
    return rows


def support_answer(nodes, edges, failed, scope, all_of=None, type_parents=None, relation_parents=None,
                   relations=RELATIONS):
    """What stands before and after a failure, by repeated passes over every node until nothing changes (no
    worklist, no counting, no region), and from it the lost and weakened nodes: their supports, the standing ones,
    the first hop to each fallen support in canonical order (forward hops by edge ID, then reverse ones) and the failed
    nodes they rest on through fallen supports; and the counts."""
    types = {n["id"]: set(n["types"]) for n in closed_nodes(nodes, type_parents)}
    edges = sorted(edges, key=lambda e: e["id"])
    names = set(relations) | {e["relation"] for e in edges}
    expanded = expand_scope(scope, relation_parents or {}, names)
    fwd = {x["relation"] for x in expanded if x["direction"] == "forward"}
    rev = {x["relation"] for x in expanded if x["direction"] == "reverse"}
    first: dict = {}
    for u in types:
        hops = [(e["id"], e["target"]) for e in edges if e["source"] == u and e["relation"] in fwd]
        hops += [(e["id"], e["source"]) for e in edges if e["target"] == u and e["relation"] in rev]
        first[u] = {}
        for eid, v in hops:
            first[u].setdefault(v, eid)
    every = set(all_of or ())
    failed = set(failed)

    def fixpoint(down):
        stands, changed = set(), True
        while changed:
            changed = False
            for u in sorted(types):
                if u in stands or u in down:
                    continue
                sup = first[u]
                ok = all(v in stands for v in sup) if every & types[u] else (not sup or any(v in stands for v in sup))
                if ok:
                    stands.add(u)
                    changed = True
        return stands

    before, after = fixpoint(set()), fixpoint(failed)
    fallen = before - after

    def causes(u):
        found, seen, todo = set(), {u}, [u]
        while todo:
            for v in first[todo.pop()]:
                if v in fallen and v in failed:
                    found.add(v)
                elif v in fallen and v not in seen:
                    seen.add(v)
                    todo.append(v)
        return sorted(found)

    def entry(u):
        sup = first[u]
        return {"supports": len(sup), "standing": sum(1 for v in sup if v in after),
                "failing": [eid for v, eid in sup.items() if v in fallen], "causes": causes(u)}

    region, changed = set(failed), True
    while changed:
        changed = False
        for u in types:
            if u not in region and set(first[u]) & region:
                region.add(u)
                changed = True
    lost = {u: entry(u) for u in sorted(fallen - failed)}
    weakened = {u: entry(u) for u in sorted(after) if set(first[u]) & fallen}
    return {"lost": lost, "weakened": weakened,
            "counts": {"lost": len(lost), "weakened": len(weakened),
                       "ungrounded": sum(1 for u in region if u not in before and u not in failed)}}
