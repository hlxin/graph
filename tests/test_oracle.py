"""Randomized parity: every operation on random small graphs (parallel edges, self-loops, cycles, non-ASCII IDs)
against independent oracles and NetworkX, with and without limits; every witness checked against the records."""

import random
from fractions import Fraction

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, verify_result
from oracle import (RELATIONS, TYPES, hops_of, least_cost, least_depths, node_id, random_graph, random_scope,
                    typed_ends)

CASES = 300


def projection(nodes, edges):
    return Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)


def random_limits(rng, op):
    out = {"max_expansions": rng.randint(0, 6)}
    if op in ("reachability", "dependency_impact"):
        out["max_results"] = rng.randint(0, 6)
    return out


def checked(p, nodes, edges, op, **params):
    res = execute(p, p.request(op, **params))
    assert res.status in (COMPLETED, INTERRUPTED), res.error
    assert verify_result(res, nodes=nodes, edges=edges) == []
    return res


@pytest.mark.parametrize("op", ["reachability", "dependency_impact"])
def test_node_sets_match_the_oracle(op):
    rng = random.Random(f"{op}-2026-10-25")
    strict, key = op == "dependency_impact", "affected" if op == "dependency_impact" else "reached"
    for _ in range(CASES):
        nodes, edges = random_graph(rng)
        p = projection(nodes, edges)
        ids = [node_id(n) for n in nodes]
        params = {"changed" if strict else "sources": rng.sample(ids, rng.randint(1, min(3, len(ids)))),
                  "scope": random_scope(rng), "max_depth": rng.choice([None, None, 0, 1, 2, 3])}
        seeds = params["changed" if strict else "sources"]
        want = least_depths(edges, seeds, params["scope"], params["max_depth"], strict)
        res = checked(p, nodes, edges, op, **params)
        assert res.status == COMPLETED
        assert {e["node"]: e["depth"] for e in res.answer[key]} == want
        member = res.affected if strict else res.reachable
        assert all(member(n) is (n in want) for n in ids)

        res = checked(p, nodes, edges, op, **params, limits=random_limits(rng, op))
        got = {e["node"]: e["depth"] for e in res.answer[key]}
        if res.status == COMPLETED:
            assert got == want
        else:                                   # what is listed is right; nothing else is decided
            assert all(want.get(n) == d for n, d in got.items())
            member = res.affected if strict else res.reachable
            assert all(member(n) is None for n in ids if n not in got)


def test_reachability_matches_networkx():
    nx = pytest.importorskip("networkx")
    rng = random.Random("networkx-reachability")
    for _ in range(CASES):
        nodes, edges = random_graph(rng)
        p = projection(nodes, edges)
        ids = [node_id(n) for n in nodes]
        sources, scope = rng.sample(ids, rng.randint(1, min(3, len(ids)))), random_scope(rng)
        max_depth = rng.choice([None, 1, 2])
        g = nx.MultiDiGraph()
        g.add_nodes_from(ids)
        for u, v, eid in hops_of(edges, scope):
            g.add_edge(u, v, key=eid)
        want: dict = {}
        for s in sources:
            for v, d in nx.single_source_shortest_path_length(g, s, cutoff=max_depth).items():
                want[v] = min(want.get(v, d), d)
        res = checked(p, nodes, edges, "reachability", sources=sources, scope=scope, max_depth=max_depth)
        assert {e["node"]: e["depth"] for e in res.answer["reached"]} == want


def random_pattern(rng):
    return [{"scope": random_scope(rng),
             "node_types": rng.choice([None, None, rng.sample(TYPES, rng.randint(1, 2))])}
            for _ in range(rng.randint(1, 4))]


def test_typed_paths_match_the_oracle():
    rng = random.Random("typed-2026-10-25")
    found = 0
    for _ in range(CASES * 2):
        nodes, edges = random_graph(rng)
        p = projection(nodes, edges)
        ids = [node_id(n) for n in nodes]
        params = {"sources": rng.sample(ids, rng.randint(1, min(2, len(ids)))), "pattern": random_pattern(rng),
                  "targets": rng.choice([None, rng.sample(ids, rng.randint(1, len(ids)))])}
        ends = typed_ends(nodes, edges, params["sources"], params["pattern"], params["targets"])
        res = checked(p, nodes, edges, "typed_path", **params)
        assert res.status == COMPLETED and res.found is bool(ends)
        if res.found:
            found += 1
            assert res.answer["witness"]["target"] in ends
        res = checked(p, nodes, edges, "typed_path", **params, limits=random_limits(rng, "typed_path"))
        if res.status == COMPLETED:
            assert res.found is bool(ends)
        else:
            assert res.found is None
    assert found > CASES // 4              # the cases exercise witnesses, not only negatives


def random_costs(rng, edges, scope):
    style = rng.choice(["small", "huge", "float"])
    pick = {"small": lambda: rng.randint(0, 5),
            "huge": lambda: 2**70 + rng.randint(0, 3),
            "float": lambda: rng.choice([0.0, 0.1, 0.2, 0.3, 1.5, 2.0**55, 1e-9])}[style]
    if rng.random() < 0.5:
        return {"by_relation": {r: pick() for r in sorted({s["relation"] for s in scope})}}
    in_scope = {s["relation"] for s in scope}
    return {"by_edge": {e["id"]: pick() for e in edges if e["relation"] in in_scope}}


def test_shortest_paths_match_the_oracle():
    nx = pytest.importorskip("networkx")
    rng = random.Random("shortest-2026-10-25")
    found = 0
    for _ in range(CASES * 2):
        nodes, edges = random_graph(rng)
        p = projection(nodes, edges)
        ids = [node_id(n) for n in nodes]
        scope = random_scope(rng)
        params = {"sources": rng.sample(ids, rng.randint(1, min(2, len(ids)))),
                  "targets": rng.sample(ids, rng.randint(1, min(3, len(ids)))), "scope": scope,
                  "costs": random_costs(rng, edges, scope)}
        want = least_cost(nodes, edges, params["sources"], params["targets"], scope, params["costs"])
        res = checked(p, nodes, edges, "shortest_path", **params)
        assert res.status == COMPLETED and res.found is (want is not None)
        exact = res.answer.get("cost_exact")
        got = None if res.answer["cost"] is None else (Fraction(exact) if exact is not None else res.answer["cost"])
        assert got == want
        found += res.found
        (kind, table), = params["costs"].items()
        if want is not None and not any(isinstance(x, float) for x in table.values()):
            rel = {e["id"]: e["relation"] for e in edges}
            g = nx.MultiDiGraph()
            g.add_nodes_from(ids)
            for u, v, eid in hops_of(edges, scope):
                g.add_edge(u, v, key=eid, cost=table[rel[eid]] if kind == "by_relation" else table[eid])
            dist = nx.multi_source_dijkstra_path_length(
                g, set(params["sources"]), weight=lambda u, v, d: min(x["cost"] for x in d.values()))
            assert min(dist[t] for t in params["targets"] if t in dist) == want

        res = checked(p, nodes, edges, "shortest_path", **params, limits=random_limits(rng, "shortest_path"))
        if res.status == COMPLETED:
            assert res.found is (want is not None) and res.answer["optimal"] in (True, None)
        else:
            assert res.found in (True, None) and res.answer["optimal"] in (False, None)
            if res.found:                       # a valid witness, an upper bound, and an honest lower bound
                exact = res.answer.get("cost_exact")
                cost = Fraction(exact) if exact is not None else res.answer["cost"]
                low = res.answer.get("lower_bound_exact")
                low = Fraction(low) if low is not None else res.answer["lower_bound"]
                assert low <= want <= cost
    assert found > CASES // 2


def test_results_do_not_depend_on_record_order():
    rng = random.Random("order")
    for _ in range(100):
        nodes, edges = random_graph(rng)
        p = projection(nodes, edges)
        ids = [node_id(n) for n in nodes]
        sh_nodes, sh_edges = nodes[:], edges[:]
        rng.shuffle(sh_nodes)
        rng.shuffle(sh_edges)
        q = projection(sh_nodes, sh_edges)
        assert q.identity == p.identity
        scope = random_scope(rng)
        for op, params in [("reachability", {"sources": ids[:2], "scope": scope}),
                           ("dependency_impact", {"changed": ids[-1:], "scope": scope}),
                           ("typed_path", {"sources": ids[:1], "pattern": random_pattern(rng)}),
                           ("shortest_path", {"sources": ids[:1], "targets": ids[-2:], "scope": scope,
                                              "costs": random_costs(rng, edges, scope)})]:
            a, b = execute(p, p.request(op, **params)), execute(q, q.request(op, **params))
            assert a.result_id == b.result_id
