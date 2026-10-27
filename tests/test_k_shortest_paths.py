"""The k shortest simple paths (Yen): the oracle enumerates every simple path from a source to a target by depth-first
search over the records, sorts them by (cost, hops, start node and edge IDs) and takes the first k; the engine must
list exactly those, in that order. networkx's shortest_simple_paths agrees on costs for simple directed graphs."""

import copy
import random
from fractions import Fraction

import pytest

from graph_runtime import COMPLETED, INTERRUPTED, Projection, execute, verify_result
from graph_runtime._canonical import thaw
from graph_runtime.result import contract_problems
from test_certificates import reseal
from oracle import RELATIONS, TYPES, hops_of, node_id, random_graph, random_scope


def every_simple_path(nodes, edges, sources, targets, scope, costs):
    """Every simple path from a source to a target, as (key, edge IDs), in the strict order of the key."""
    kind, table = next(iter(costs.items())) if costs else (None, None)
    rational = table is not None and any(isinstance(x, float) for x in table.values())
    rel = {e["id"]: e["relation"] for e in edges}

    def cost(eid):
        if table is None:
            return 1
        x = table[rel[eid] if kind == "by_relation" else eid]
        return Fraction(x) if rational else x
    out_of = {}
    for u, v, eid in hops_of(edges, scope):
        out_of.setdefault(u, []).append((eid, v))
    found = []

    def go(at, visited, steps, total):
        if at in targets:
            found.append(((total, len(steps), (visited[0],) + tuple(steps)), list(steps)))
        for eid, v in out_of.get(at, ()):
            if v not in visited:
                go(v, visited + [v], steps + [eid], total + cost(eid))
    for s in sorted(set(sources)):
        go(s, [s], [], 0)
    found.sort(key=lambda f: f[0])
    return found


def random_costs(rng, edges):
    if rng.random() < 0.3:
        return None
    pick = (lambda: rng.randint(0, 3)) if rng.random() < 0.7 else (lambda: rng.choice([0, 0.5, 1.25, 2]))
    if rng.random() < 0.5:
        return {"by_relation": {r: pick() for r in RELATIONS}}
    return {"by_edge": {e["id"]: pick() for e in edges}}


def run(rng, n=7, m=14):
    nodes, edges = random_graph(rng, max_nodes=n, max_edges=m)
    ids = [node_id(x) for x in nodes]
    p = Projection.build(nodes=nodes, edges=edges, relations=RELATIONS, node_types=TYPES)
    raw = {"operation": "k_shortest_paths", "sources": rng.sample(ids, rng.randint(1, min(2, len(ids)))),
           "targets": rng.sample(ids, rng.randint(1, min(2, len(ids)))), "scope": random_scope(rng),
           "costs": random_costs(rng, edges), "k": rng.randint(1, 12)}
    return nodes, edges, p, raw


def listed(res):
    return [[h["edge"] for h in x["hops"]] for x in res.answer["paths"]]


def test_the_paths_are_the_first_k_in_order():
    rng = random.Random("k shortest")
    tally = {"exhausted": 0, "full": 0, "several": 0}
    for _ in range(700):
        nodes, edges, p, raw = run(rng)
        res = execute(p, {**raw, "projection": p.identity})
        assert res.status == COMPLETED, res.error
        want = every_simple_path(nodes, edges, set(raw["sources"]), set(raw["targets"]), raw["scope"],
                                 raw["costs"])[:raw["k"]]
        assert listed(res) == [steps for _, steps in want], raw
        assert res.answer["exhausted"] is (len(want) < raw["k"])
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        tally["exhausted" if res.answer["exhausted"] else "full"] += 1
        tally["several"] += len(want) > 2
    assert min(tally.values()) >= 50, tally


def test_networkx_agrees_on_costs():
    nx = pytest.importorskip("networkx")
    from itertools import islice
    rng = random.Random("yen networkx")
    for _ in range(20):
        n = 30
        pairs = sorted({(rng.randrange(n), rng.randrange(n)) for _ in range(110)})
        pairs = [(a, b) for a, b in pairs if a != b]
        nodes = [f"v{i:02d}" for i in range(n)]
        edges = [{"id": f"e{k:03d}", "source": nodes[a], "target": nodes[b], "relation": "R"}
                 for k, (a, b) in enumerate(pairs)]
        table = {e["id"]: rng.randint(1, 9) for e in edges}
        g = nx.DiGraph()
        g.add_nodes_from(nodes)
        for e in edges:
            g.add_edge(e["source"], e["target"], weight=table[e["id"]])
        s, t = rng.sample(nodes, 2)
        p = Projection.build(nodes=nodes, edges=edges)
        res = execute(p, p.request("k_shortest_paths", sources=[s], targets=[t], scope=[("R", "forward")],
                                   costs={"by_edge": table}, k=15))
        try:
            want = [nx.path_weight(g, path, "weight") for path in islice(nx.shortest_simple_paths(g, s, t, "weight"),
                                                                          15)]
        except nx.NetworkXNoPath:
            want = []
        assert [x["cost"] for x in res.answer["paths"]] == want
        assert verify_result(res, nodes=nodes, edges=edges) == []


def test_forged_paths_are_refused():
    nodes = ["s", "a", "b", "t"]
    edges = [{"id": "e1", "source": "s", "target": "a", "relation": "R"},
             {"id": "e2", "source": "a", "target": "t", "relation": "R"},
             {"id": "e3", "source": "s", "target": "b", "relation": "R"},
             {"id": "e4", "source": "b", "target": "t", "relation": "R"},
             {"id": "e5", "source": "a", "target": "b", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("k_shortest_paths", sources=["s"], targets=["t"], scope=[("R", "forward")], k=5))
    assert listed(res) == [["e1", "e2"], ["e3", "e4"], ["e1", "e5", "e4"]] and res.answer["exhausted"] is True

    def forged(change):
        data = copy.deepcopy(thaw(res.to_dict()))
        change(data["answer"])
        return verify_result(reseal(data), nodes=nodes, edges=edges)
    assert forged(lambda a: a["paths"].reverse())                                   # out of order
    assert forged(lambda a: a["paths"].__setitem__(1, copy.deepcopy(a["paths"][0])))   # a repeat
    assert forged(lambda a: a["paths"][0].__setitem__("cost", 1))
    assert forged(lambda a: a.__setitem__("exhausted", False))


def test_an_interrupted_search_lists_valid_paths_in_order():
    rng = random.Random("yen interrupted")
    seen = set()
    for _ in range(300):
        nodes, edges, p, raw = run(rng)
        res = execute(p, {**raw, "projection": p.identity, "limits": {"max_expansions": rng.randint(0, 25)}})
        assert verify_result(res, nodes=nodes, edges=edges) == [] and contract_problems(res.to_dict()) == []
        if res.status == INTERRUPTED:
            want = every_simple_path(nodes, edges, set(raw["sources"]), set(raw["targets"]), raw["scope"],
                                     raw["costs"])
            assert listed(res) == [steps for _, steps in want][:len(listed(res))]      # a prefix of the order
            assert res.answer["exhausted"] is None
        seen.add(res.status)
    assert seen == {COMPLETED, INTERRUPTED}


def test_kpaths_results_validate_against_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from graph_runtime import json_schema
    from graph_runtime._validation import shape_problems
    validator = jsonschema.Draft202012Validator(json_schema("result"))
    rng = random.Random("yen schema")
    for _ in range(80):
        nodes, edges, p, raw = run(rng)
        for limits in ({}, {"max_expansions": rng.randint(0, 9)}):
            data = json.loads(json.dumps(thaw(execute(p, {**raw, "projection": p.identity, "limits": limits})
                                              .to_dict())))
            assert [e.message for e in validator.iter_errors(data)] == [] and shape_problems("result", data) == []


def test_a_long_path_needs_no_quadratic_memory():
    import tracemalloc
    n = 6000
    nodes = [f"v{i:05d}" for i in range(n)]
    edges = [{"id": f"e{i:05d}", "source": nodes[i], "target": nodes[i + 1], "relation": "R"} for i in range(n - 1)]
    p = Projection.build(nodes=nodes, edges=edges)
    request = p.request("k_shortest_paths", sources=[nodes[0]], targets=[nodes[-1]], scope=[("R", "forward")], k=1)
    tracemalloc.start()
    res = execute(p, request)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert res.status == COMPLETED and res.answer["paths"][0]["length"] == n - 1
    assert peak < 40_000_000, peak                         # settled keys are released: no n-squared prefixes


def test_the_contract_alone_refuses_a_misordered_or_repeated_list():
    from graph_runtime import Result
    nodes = ["s", "t"]
    edges = [{"id": "cheap", "source": "s", "target": "t", "relation": "R"},
             {"id": "dear", "source": "s", "target": "t", "relation": "R"}]
    p = Projection.build(nodes=nodes, edges=edges)
    res = execute(p, p.request("k_shortest_paths", sources=["s"], targets=["t"], scope=[("R", "forward")],
                               costs={"by_edge": {"cheap": 1, "dear": 2}}, k=2))
    assert [x["cost"] for x in res.answer["paths"]] == [1, 2]
    for change in (lambda a: a["paths"].reverse(), lambda a: a["paths"].__setitem__(1, copy.deepcopy(a["paths"][0]))):
        data = copy.deepcopy(thaw(res.to_dict()))
        change(data["answer"])
        data = reseal(data)
        assert contract_problems(data)
        with pytest.raises(ValueError):
            Result.from_dict(data)
