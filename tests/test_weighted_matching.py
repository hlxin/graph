"""Cheapest largest matchings (the assignment problem): successive shortest paths with potentials. A brute force over
every matching gives the largest size and the least cost at that size; networkx's maximum-cardinality weighted
matching agrees on larger graphs; König's cover and the residual potentials prove each answer, and forged ones are
refused."""

import copy
import random
from fractions import Fraction

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.result import contract_problems
from graph_runtime.revalidate import FAILS, HOLDS, diff, revalidate
from test_certificates import reseal
from oracle import RELATIONS, TYPES, node_id, random_graph, random_scope
from test_revalidate import edited


def candidates(nodes, edges, left, scope, costs):
    """Each left node's hops in scope to nodes outside the left set, with their exact costs."""
    kind, table = next(iter(costs.items()))
    rational = any(isinstance(x, float) for x in table.values())
    allowed = {(s["relation"], s["direction"]) if isinstance(s, dict) else tuple(s) for s in scope}
    out = {u: [] for u in left}
    for e in edges:
        c = table[e["relation"] if kind == "by_relation" else e["id"]]
        c = Fraction(c) if rational else c
        for direction, a, b in (("forward", e["source"], e["target"]), ("reverse", e["target"], e["source"])):
            if (e["relation"], direction) in allowed and a in left and b not in left:
                out[a].append((b, c))
    return out


def cheapest_largest(nodes, edges, left, scope, costs):
    """(size, cost) by trying every matching."""
    cand = candidates(nodes, edges, left, scope, costs)
    order = sorted(left)
    best = (0, 0)

    def go(i, used, size, cost):
        nonlocal best
        if i == len(order):
            if size > best[0] or size == best[0] and cost < best[1]:
                best = (size, cost)
            return
        go(i + 1, used, size, cost)
        for v, c in cand[order[i]]:
            if v not in used:
                go(i + 1, used | {v}, size + 1, cost + c)
    go(0, frozenset(), 0, 0)
    return best


def random_costs(rng, edges):
    pick = (lambda: rng.randint(-4, 6)) if rng.random() < 0.7 else (lambda: rng.choice([-1.5, 0.25, 2, 3.75]))
    if rng.random() < 0.5:
        return {"by_relation": {r: pick() for r in RELATIONS}}
    return {"by_edge": {e["id"]: pick() for e in edges}}


def run(rng):
    nodes, edges = random_graph(rng, max_nodes=8, max_edges=16)
    p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
    raw = {"operation": "matching", "left": {"node_types": ["A"]}, "scope": random_scope(rng),
           "costs": random_costs(rng, edges), "certificate": rng.random() < 0.8}
    left = {node_id(n) for n in nodes if isinstance(n, dict) and "A" in n["types"]}
    return nodes, edges, p, raw, left


def cost_of(a):
    return Fraction(a["cost_exact"]) if "cost_exact" in a else a["cost"]


def test_the_matching_is_a_cheapest_largest_one_and_proves_itself():
    rng = random.Random("weighted matching")
    sizes = set()
    for _ in range(600):
        nodes, edges, p, raw, left = run(rng)
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        size, cost = cheapest_largest(nodes, edges, left, raw["scope"], raw["costs"])
        assert (res.answer["size"], cost_of(res.answer)) == (size, cost), raw
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        sizes.add(min(size, 3))
    assert sizes == {0, 1, 2, 3}


def test_networkx_agrees_on_larger_assignments():
    nx = pytest.importorskip("networkx")
    rng = random.Random("networkx assignment")
    for _ in range(25):
        lefts = [f"l{i:02d}" for i in range(30)]
        rights = [f"r{i:02d}" for i in range(25)]
        nodes = [{"id": x, "types": ["L"]} for x in lefts] + rights
        edges, g = [], nx.Graph()
        for k in range(160):
            u, v, c = rng.choice(lefts), rng.choice(rights), rng.randint(-20, 40)
            edges.append({"id": f"e{k:03d}", "source": u, "target": v, "relation": "R", "c": c})
            if not g.has_edge(u, v) or c < 1000 - g[u][v]["weight"]:
                g.add_edge(u, v, weight=1000 - c)                   # the cheapest of parallel hops
        p = Projection.build(nodes=nodes, edges=edges)
        res = execute(p, p.request("matching", left={"node_types": ["L"]}, scope=[("R", "forward")],
                                   costs={"by_edge": {e["id"]: e["c"] for e in edges}}, certificate=True))
        m = nx.max_weight_matching(g, maxcardinality=True)
        assert res.answer["size"] == len(m)
        assert res.answer["cost"] == sum(1000 - g[u][v]["weight"] for u, v in m)
        assert verify_result(res, nodes=nodes, edges=edges) == []


def test_forged_matchings_are_refused():
    nodes = [{"id": "l1", "types": ["L"]}, {"id": "l2", "types": ["L"]}, "r1", "r2"]
    edges = [{"id": "a", "source": "l1", "target": "r1", "relation": "R"},
             {"id": "b", "source": "l1", "target": "r2", "relation": "R"},
             {"id": "c", "source": "l2", "target": "r1", "relation": "R"},
             {"id": "d", "source": "l2", "target": "r2", "relation": "R"}]
    costs = {"by_edge": {"a": 1, "b": 5, "c": 5, "d": 2}}
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("matching", left={"node_types": ["L"]}, scope=[("R", "forward")], costs=costs,
                               certificate=True))
    assert res.answer["cost"] == 3 and verify_result(res, nodes=nodes, edges=edges) == []

    def forged(change):
        data = copy.deepcopy(thaw(res.to_dict()))
        change(data["answer"])
        return verify_result(reseal(data), nodes=nodes, edges=edges)

    def dearer(a):                                         # as large, but dearer, under the cheap pair's certificate
        a["pairs"] = [{"left": "l1", "right": "r2", "edge": "b", "relation": "R", "direction": "forward"},
                      {"left": "l2", "right": "r1", "edge": "c", "relation": "R", "direction": "forward"}]
        a["cost"] = 10
    assert forged(dearer)
    assert forged(lambda a: a.__setitem__("cost", 2))
    assert forged(lambda a: a["certificate"]["potentials"][0].__setitem__("value", 100))
    assert forged(lambda a: a["certificate"].__setitem__("sink", -100))     # the sink below its paired partners


def test_an_interrupted_search_keeps_a_valid_matching():
    rng = random.Random("weighted interrupted")
    seen = set()
    for _ in range(300):
        nodes, edges, p, raw, left = run(rng)
        res = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 10)}})
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        if res.status == INTERRUPTED:
            assert res.answer["optimal"] is False and res.answer["certificate"] is None
        seen.add(res.status)
    assert seen == {COMPLETED, INTERRUPTED}


def test_revalidation_agrees_with_fresh_runs():
    rng = random.Random("weighted revalidation")
    tally = {}
    for _ in range(300):
        nodes, edges, p, raw, left = run(rng)
        raw["certificate"] = True
        raw["costs"] = {"by_relation": raw["costs"].get("by_relation") or {r: 1 for r in RELATIONS}}
        nodes2, edges2 = edited(rng, nodes, edges)
        q = Projection.build(nodes=nodes2, edges=edges2, relations=RELATIONS, node_types=TYPES)
        old = execute(p, {**raw, "projection": p.identity})
        verdict = revalidate(old, q, diff=diff(p, q)).verdict
        fresh = execute(q, {**raw, "projection": q.identity})
        if verdict == HOLDS:
            assert (fresh.answer["size"], cost_of(fresh.answer)) == (old.answer["size"], cost_of(old.answer))
        elif verdict == FAILS:
            assert verify_result(old, nodes=nodes2, edges=edges2)
        tally[verdict] = tally.get(verdict, 0) + 1
    assert tally.get(HOLDS, 0) >= 20 and tally.get(FAILS, 0) >= 20, tally


def test_weighted_results_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from graph_runtime import json_schema
    from graph_runtime._validation import shape_problems
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    rng = random.Random("weighted schema")
    for _ in range(80):
        nodes, edges, p, raw, left = run(rng)
        for limits in ({}, {"max_expansions": rng.randint(0, 6)}):
            data = json.loads(json.dumps(thaw(execute(p, {**raw, "projection": p.identity, "limits": limits})
                                              .to_dict())))
            assert [e.message for e in validator.iter_errors(data)] == [] and shape_problems("result", data) == []


def test_potentials_are_exact_numbers():
    """Integral floats such as 1e100 would swallow a negative reduced cost (-2 + 1e100 - 1e100 == 0 in floats): an
    integer-cost certificate's potentials must be integers, and a forged one made of floats is refused."""
    nodes = [{"id": "l", "types": ["L"]}, "r"]
    edges = [{"id": "cheap", "source": "l", "target": "r", "relation": "R"},
             {"id": "dear", "source": "l", "target": "r", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("matching", left={"node_types": ["L"]}, scope=[("R", "forward")],
                               costs={"by_edge": {"cheap": 1, "dear": 2}}, certificate=True))
    assert res.answer["cost"] == 1 and verify_result(res, nodes=nodes, edges=edges) == []
    data = copy.deepcopy(thaw(res.to_dict()))
    a = data["answer"]
    a["pairs"][0]["edge"], a["cost"] = "dear", 2
    for e in a["certificate"]["potentials"]:
        e["value"] = 1e100
    a["certificate"]["source"] = a["certificate"]["sink"] = 1e100
    data = reseal(data)
    assert contract_problems(data) and verify_result(data, nodes=nodes, edges=edges)
